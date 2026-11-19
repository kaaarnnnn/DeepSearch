"""Сканер файловой системы: многопоточный обход, пакетная запись в SQLite, возобновление, глубокий анализ.

Архитектура:
  * N потоков-«ходоков» берут каталоги из LIFO-очереди (обход в глубину — очередь остаётся маленькой),
    читают их через os.scandir и отправляют пачки строк в очередь записи;
  * один поток-«писатель» складывает пачки в SQLite транзакциями (upsert — инкрементальное обновление);
  * управляющий поток следит за прогрессом, сохраняет контрольные точки (для продолжения) и скорость;
  * фаза «Глубокое сканирование»: пул потоков читает заголовки/содержимое, определяет тип по сигнатуре,
    считает SHA-256 и извлекает текст для полнотекстового поиска.
Ошибки доступа не прерывают работу: они записываются в таблицу errors и показываются пользователю.
"""
from __future__ import annotations

import hashlib
import logging
import os
import queue
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import content as content_mod
import filetypes as ft
import winutils as wu
from database import UPSERT_FILE, Database
from settings import Settings

log = logging.getLogger("deepsearch.scanner")

ACCESS_OK, ACCESS_DENIED, ACCESS_LOCKED, ACCESS_CLOUD, ACCESS_ERROR = 1, 0, 2, 3, 4
ACCESS_TEXT = {ACCESS_OK: "Доступен", ACCESS_DENIED: "Нет доступа", ACCESS_LOCKED: "Заблокирован",
               ACCESS_CLOUD: "Только в облаке", ACCESS_ERROR: "Ошибка чтения"}

ERROR_KIND_TEXT = {
    "access": "Отказано в доступе", "locked": "Файл занят другой программой", "offline": "Диск/путь недоступен",
    "missing": "Исчез во время сканирования", "special": "Специальная ссылка Windows",
    "longpath": "Слишком длинный путь", "other": "Прочая ошибка",
}


def classify_error(exc: BaseException) -> tuple[str, str]:
    """-> (вид ошибки, понятное описание)."""
    code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    detail = f" (код {code})" if code else ""
    if isinstance(exc, PermissionError) or code in (5, 13):
        return "access", "Отказано в доступе — у текущего пользователя нет прав" + detail
    if wu.IS_WIN:
        if code in (32, 33):
            return "locked", "Файл заблокирован другой программой" + detail
        if code in (21, 53, 55, 64, 67, 1167, 1005):
            return "offline", "Диск или сетевой путь недоступен/отключён" + detail
        if code in (2, 3):
            return "missing", "Файл или папка исчезли во время сканирования" + detail
        if code in (1920, 1921):
            return "special", "Специальная ссылка Windows недоступна для чтения" + detail
        if code == 206:
            return "longpath", "Слишком длинный путь" + detail
    else:
        if code == 16:
            return "locked", "Ресурс занят" + detail
        if code in (2,):
            return "missing", "Файл или папка исчезли во время сканирования" + detail
        if code in (5, 19, 6):
            return "offline", "Устройство недоступно" + detail
    return "other", f"{type(exc).__name__}: {exc}"


class Progress:
    """Разделяемое состояние сканирования (читается интерфейсом по таймеру)."""

    def __init__(self):
        self.phase = "idle"          # idle / walk / deep / done / stopped / failed
        self.status_text = ""
        self.current = ""
        self.dirs = 0
        self.files = 0
        self.bytes = 0
        self.errors = 0
        self.links_skipped = 0
        self.speed = 0.0             # объектов (файлов или папок) в секунду
        self.expected_bytes = 0
        self.deep_total = 0
        self.deep_done = 0
        self.queue_size = 0
        self.started = 0.0
        self.elapsed = 0.0
        self.mode = ""
        self.fraction: Optional[float] = None   # None — неопределённый прогресс
        self.error_text = ""

    def copy(self) -> "Progress":
        c = Progress.__new__(Progress)
        c.__dict__.update(self.__dict__)
        return c


class ScanEngine:
    def __init__(self, db: Database, settings: Settings, mode: str, roots: list[str], resume: bool = False):
        self.db = db
        self.settings = settings
        self.mode = mode
        self.roots = [wu.normalize_root(r) for r in roots]
        self.resume = resume
        self.progress = Progress()
        self.progress.mode = mode
        self.stop_ev = threading.Event()
        self.done_ev = threading.Event()
        self._lock = threading.Lock()
        self._wq: queue.Queue = queue.Queue(maxsize=256)
        self._dq: queue.LifoQueue = queue.LifoQueue()
        self._pending = 0
        self._inflight: dict[int, str] = {}
        self._skipped: list[str] = []
        self._gen = 0
        self._cleanup_ok = True
        self._walk_complete = False
        self._thread: Optional[threading.Thread] = None
        self._excl_names = {n.lower() for n in settings.exclude_names}
        self._excl_paths = [os.path.normcase(os.path.normpath(p)) for p in settings.exclude_paths]
        if mode == "quick":
            self._excl_names |= {n.lower() for n in settings.quick_exclude_names}
        self._skip_hidden_dirs = mode == "quick" and settings.quick_skip_hidden_dirs
        self._deep = mode == "deep"

    # ------------------------------------------------------------------ public API
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="scan-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_ev.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    @property
    def finished(self) -> bool:
        return self.done_ev.is_set()

    def snapshot(self) -> Progress:
        p = self.progress.copy()
        p.queue_size = self._dq.qsize()
        if p.started:
            p.elapsed = time.time() - p.started
        return p

    # ------------------------------------------------------------------ управляющий поток
    def _run(self) -> None:
        p = self.progress
        p.started = time.time()
        writer = None
        try:
            phase, start_dirs = self._prepare()
            writer = threading.Thread(target=self._writer_loop, name="scan-writer", daemon=True)
            writer.start()
            if phase == "walk":
                p.phase = "walk"
                self._walk(start_dirs)
            if not self.stop_ev.is_set() and self._deep:
                p.phase = "deep"
                self._set_session(phase="deep")
                self._deep_phase()
            self._finalize()
        except Exception as exc:
            log.exception("Сбой сканирования")
            p.phase = "failed"
            p.error_text = f"{type(exc).__name__}: {exc}"
            try:
                self._call_writer(lambda c: self._save_session(c, "failed", p.phase))
            except Exception:
                pass
        finally:
            if writer is not None:
                self._wq.put(None)
                writer.join(timeout=120)
            self.done_ev.set()

    def _prepare(self) -> tuple[str, list[str]]:
        p = self.progress
        conn = self.db.connect()
        try:
            if self.resume:
                s = self.db.get_session()
                if not s:
                    raise RuntimeError("Нет прерванного сканирования для продолжения")
                self.mode, self.roots, self._gen = s["mode"], s["roots"], s["gen"]
                p.mode = self.mode
                self._deep = self.mode == "deep"
                self._cleanup_ok = False          # список пропущенных каталогов утрачен
                st = s["stats"]
                p.dirs, p.files, p.bytes, p.errors = st.get("dirs", 0), st.get("files", 0), st.get("bytes", 0), st.get("errors", 0)
                phase = s["phase"]
                dirs = self.db.pending_dirs() if phase != "deep" else []
            else:
                gen = (self.db.get_meta("gen", 0) or 0) + 1
                self.db.set_meta("gen", gen, conn)
                self._gen = gen
                conn.execute("DELETE FROM errors")
                conn.execute("DELETE FROM pending_dirs")
                phase, dirs = "walk", []
                for r in self.roots:
                    if not os.path.isdir(wu.to_long_path(r)):
                        self._report_error(r, FileNotFoundError(2, "Корень сканирования недоступен"), force_kind="offline")
                        continue
                    dirs.append(r)
            conn.execute("DELETE FROM session")
            conn.execute("INSERT INTO session(id,mode,roots,gen,status,phase,started,stats) VALUES(1,?,?,?,?,?,?,?)",
                         (self.mode, _j(self.roots), self._gen, "running", phase, int(p.started), "{}"))
            self.db.set_meta("last_roots", self.roots, conn)
            self.db.set_meta("last_mode", self.mode, conn)
        finally:
            conn.close()
        # Оценка объёма для процентов прогресса: занятое место на дисках-корнях
        exp = 0
        for r in self.roots:
            if os.path.splitdrive(r)[1] in ("\\", "/") or r == "/":
                try:
                    exp += shutil.disk_usage(r).used
                except OSError:
                    pass
        p.expected_bytes = exp
        return phase, dirs

    # ------------------------------------------------------------------ фаза 1: обход
    def _walk(self, start_dirs: list[str]) -> None:
        p = self.progress
        for d in start_dirs:
            self._dq.put(d)
        self._pending = len(start_dirs)
        n = self.settings.worker_threads()
        threads = [threading.Thread(target=self._walker, name=f"walker-{i}", daemon=True) for i in range(n)]
        for t in threads:
            t.start()
        last_t, last_n, last_ckpt = time.time(), p.files, time.time()
        ema = 0.0
        while any(t.is_alive() for t in threads):
            time.sleep(0.25)
            now = time.time()
            cur = p.files
            dt = now - last_t
            if dt >= 0.5:
                inst = (cur - last_n) / dt
                ema = inst if ema == 0 else ema * 0.6 + inst * 0.4
                p.speed = ema
                last_t, last_n = now, cur
            if p.expected_bytes:
                p.fraction = min(0.99, p.bytes / p.expected_bytes)
            else:
                p.fraction = None
            if now - last_ckpt > 5:
                last_ckpt = now
                self._checkpoint()
        if self.stop_ev.is_set() and self._pending_list():
            self._checkpoint(final=True)
        else:
            self._walk_complete = True              # обход закончен (даже если «Стоп» нажат в самом конце)
            self._wq.put(("call", self._clear_pending))
            if self._cleanup_ok:
                self._wq.put(("call", self._cleanup_stale))
            if p.expected_bytes:
                p.fraction = 1.0

    def _pending_list(self) -> list[str]:
        with self._dq.mutex:
            queued = list(self._dq.queue)
        return list(dict.fromkeys(queued + list(self._inflight.values())))

    def _checkpoint(self, final: bool = False) -> None:
        pend = self._pending_list()
        stats = {"dirs": self.progress.dirs, "files": self.progress.files, "bytes": self.progress.bytes,
                 "errors": self.progress.errors}

        def save(conn: sqlite3.Connection):
            conn.execute("DELETE FROM pending_dirs")
            conn.executemany("INSERT OR IGNORE INTO pending_dirs(path) VALUES(?)", ((d,) for d in pend))
            conn.execute("UPDATE session SET stats=? WHERE id=1", (_j(stats),))
        self._wq.put(("call", save))

    def _walker(self) -> None:
        tid = threading.get_ident()
        while not self.stop_ev.is_set():
            try:
                d = self._dq.get(timeout=0.1)
            except queue.Empty:
                if self._pending <= 0:
                    return
                continue
            self._inflight[tid] = d
            subdirs: list[str] = []
            try:
                subdirs = self._scan_dir(d)
            except Exception:                      # непредвиденная ошибка в одном каталоге не должна ронять обход
                log.exception("Сбой при обработке каталога %s", d)
            with self._lock:
                for s in subdirs:
                    self._dq.put(s)
                self._pending += len(subdirs) - 1
            self._inflight.pop(tid, None)

    def _skip_dir(self, name: str, full: str, entry: os.DirEntry) -> bool:
        low = name.lower()
        if low in self._excl_names:
            self._add_skipped(full)
            return True
        if self._excl_paths:
            norm = os.path.normcase(full)
            if any(norm == ep or norm.startswith(ep + os.sep) for ep in self._excl_paths):
                self._add_skipped(full)
                return True
        try:
            is_link = entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction())
        except OSError:
            is_link = False
        if is_link:                     # символические ссылки и junction не разворачиваем: защита от циклов
            with self._lock:
                self.progress.links_skipped += 1
            self._add_skipped(full)
            return True
        if self._skip_hidden_dirs:
            try:
                attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
            except OSError:
                attrs = 0
            if (attrs & (wu.ATTR_HIDDEN | wu.ATTR_SYSTEM)) or (not wu.IS_WIN and name.startswith(".")):
                self._add_skipped(full)
                return True
        return False

    def _add_skipped(self, full: str) -> None:
        with self._lock:
            if len(self._skipped) < 2000:
                self._skipped.append(full)
            else:
                self._cleanup_ok = False

    def _scan_dir(self, d: str) -> list[str]:
        p = self.progress
        p.current = d
        base = d if d.endswith(os.sep) else d + os.sep
        rows: list[tuple] = []
        subdirs: list[str] = []
        nbytes = 0
        complete = True
        try:
            it = os.scandir(wu.to_long_path(d))
        except OSError as exc:
            self._report_error(d, exc)
            with self._lock:
                p.dirs += 1
            return []
        win = wu.IS_WIN
        with it:
            while True:
                try:
                    e = next(it)
                except StopIteration:
                    break
                except OSError as exc:
                    self._report_error(d, exc)
                    complete = False
                    break
                try:
                    name = e.name
                    is_symlink = e.is_symlink()
                    if is_symlink:
                        is_dir = e.is_dir()          # ссылка на каталог — не заходим
                        if is_dir:
                            self._skip_dir(name, base + name, e)
                            continue
                    else:
                        is_dir = e.is_dir(follow_symlinks=False)
                    if is_dir:
                        full = base + name
                        if not self._skip_dir(name, full, e):
                            subdirs.append(full)
                        continue
                    st = e.stat(follow_symlinks=False)
                    attrs = getattr(st, "st_file_attributes", 0)
                    size = st.st_size
                    dot = name.rfind(".")
                    ext = name[dot + 1:].lower() if dot != -1 else ""
                    flags = 0
                    if win:
                        if attrs & wu.ATTR_HIDDEN:
                            flags |= ft.F_HIDDEN
                        if attrs & wu.ATTR_SYSTEM:
                            flags |= ft.F_SYSTEM
                        if attrs & wu.ATTR_READONLY:
                            flags |= ft.F_READONLY
                        if attrs & wu.ATTR_TEMPORARY:
                            flags |= ft.F_TEMP
                    else:
                        if name.startswith("."):
                            flags |= ft.F_HIDDEN
                        if not (st.st_mode & 0o200):
                            flags |= ft.F_READONLY
                    if not ext:
                        flags |= ft.F_NOEXT
                    if ft.is_temp_name(name, ext):
                        flags |= ft.F_TEMP
                    if is_symlink:
                        flags |= ft.F_LINK
                    access = ACCESS_OK
                    if attrs & wu.ATTR_CLOUD_MASK:
                        flags |= ft.F_CLOUD
                        access = ACCESS_CLOUD
                    ctime = int(getattr(st, "st_birthtime", None) or st.st_ctime)
                    rows.append((base + name, name, name.lower(), ext, size, ctime, int(st.st_mtime),
                                 ft.category_for(ext) if ext else "other", flags, access))
                    nbytes += size
                except OSError as exc:
                    self._report_error(base + getattr(e, "name", ""), exc)
        with self._lock:
            p.dirs += 1
            p.files += len(rows)
            p.bytes += nbytes
        self._wq.put(("dir", d, rows, complete))
        return subdirs

    # ------------------------------------------------------------------ ошибки
    def _report_error(self, path: str, exc: BaseException, force_kind: Optional[str] = None) -> None:
        kind, msg = classify_error(exc)
        if force_kind:
            kind = force_kind
        with self._lock:
            self.progress.errors += 1
        self._wq.put(("err", path, kind, msg))

    # ------------------------------------------------------------------ писатель БД
    def _writer_loop(self) -> None:
        conn = self.db.connect()
        conn.execute("BEGIN")
        gen = self._gen
        pending_rows, last_commit = 0, time.time()
        try:
            while True:
                try:
                    item = self._wq.get(timeout=0.5)
                except queue.Empty:
                    item = "idle"
                if item is None:
                    break
                try:
                    if item == "idle":
                        pass
                    elif item[0] == "dir":
                        pending_rows += self._write_dir(conn, item, gen)
                    elif item[0] == "err":
                        conn.execute("INSERT INTO errors(path,kind,message,ts) SELECT ?,?,?,? "
                                     "WHERE (SELECT COUNT(*) FROM errors) < 300000",
                                     (item[1], item[2], item[3], int(time.time())))
                        pending_rows += 1
                    elif item[0] == "deep":
                        self._write_deep(conn, item[1])
                        pending_rows += 1
                    elif item[0] == "call":
                        item[1](conn)
                        pending_rows += 1
                    elif item[0] == "flush":
                        conn.execute("COMMIT")
                        conn.execute("BEGIN")
                        item[1].set()
                except sqlite3.Error as exc:
                    log.error("Ошибка записи в БД: %s", exc)
                now = time.time()
                if pending_rows >= 30000 or (pending_rows and now - last_commit > 1.5) or (item == "idle" and pending_rows):
                    conn.execute("COMMIT")
                    conn.execute("BEGIN")
                    pending_rows, last_commit = 0, now
            conn.execute("COMMIT")
            try:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:
                pass
        finally:
            conn.close()

    @staticmethod
    def _write_dir(conn: sqlite3.Connection, item: tuple, gen: int) -> int:
        _, d, rows, complete = item
        conn.execute("INSERT INTO dirs(path) VALUES(?) ON CONFLICT(path) DO NOTHING", (d,))
        dir_id = conn.execute("SELECT id FROM dirs WHERE path=?", (d,)).fetchone()[0]
        if rows:
            conn.executemany(UPSERT_FILE, [(r[0], dir_id, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], gen)
                                           for r in rows])
        if complete:                         # файлы, исчезшие из этого каталога
            conn.execute("DELETE FROM files WHERE dir_id=? AND gen<?", (dir_id, gen))
        return len(rows) + 1

    @staticmethod
    def _write_deep(conn: sqlite3.Connection, upd: dict) -> None:
        conn.execute("UPDATE files SET sha256=COALESCE(?,sha256), sig=?, mismatch=?, category=?, access=?, deep=1 "
                     "WHERE id=?", (upd["sha256"], upd["sig"], upd["mismatch"], upd["category"], upd["access"], upd["id"]))
        if upd.get("text"):
            conn.execute("DELETE FROM content_fts WHERE rowid=?", (upd["id"],))
            conn.execute("INSERT INTO content_fts(rowid, text) VALUES(?,?)", (upd["id"], upd["text"]))

    def _call_writer(self, fn: Callable[[sqlite3.Connection], None]) -> None:
        self._wq.put(("call", fn))

    def _flush_writer(self) -> None:
        ev = threading.Event()
        self._wq.put(("flush", ev))
        ev.wait(timeout=300)

    def _clear_pending(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM pending_dirs")

    def _cleanup_stale(self, conn: sqlite3.Connection) -> None:
        """Удаляет из индекса файлы из исчезнувших папок (внутри полностью пройденных корней)."""
        gen = self._gen
        skipped = list(self._skipped)
        hi_mark = "\U0010FFFF"
        for root in self.roots:
            prefix = root if root.endswith(os.sep) else root + os.sep
            sql = "DELETE FROM files WHERE gen<? AND path>=? AND path<?"
            params: list = [gen, prefix, prefix + hi_mark]
            for sk in skipped:
                if sk.startswith(prefix):
                    sp = sk + os.sep
                    sql += " AND NOT (path>=? AND path<?)"
                    params += [sp, sp + hi_mark]
            conn.execute(sql, params)
        if self.db.has_fts:
            try:
                conn.execute("DELETE FROM content_fts WHERE rowid NOT IN (SELECT id FROM files)")
            except sqlite3.Error:
                pass

    def _save_session(self, conn: sqlite3.Connection, status: str, phase: str) -> None:
        p = self.progress
        stats = {"dirs": p.dirs, "files": p.files, "bytes": p.bytes, "errors": p.errors}
        conn.execute("UPDATE session SET status=?, phase=?, finished=?, stats=? WHERE id=1",
                     (status, phase, int(time.time()), _j(stats)))

    def _set_session(self, phase: str) -> None:
        self._call_writer(lambda c: c.execute("UPDATE session SET phase=? WHERE id=1", (phase,)))

    def _finalize(self) -> None:
        p = self.progress
        stopped = self.stop_ev.is_set()
        phase_now = p.phase
        if stopped and phase_now == "walk" and self._walk_complete:
            if self._deep:
                phase_now = "deep"            # обход завершён, прервано до глубокого анализа: продолжение начнётся с него
            else:
                stopped = False               # фактически всё уже просканировано
        p.phase = "stopped" if stopped else "done"
        status = "interrupted" if stopped else "done"
        self._call_writer(lambda c: self._save_session(c, status, phase_now if stopped else "done"))
        self._flush_writer()
        if p.expected_bytes and not stopped:
            p.fraction = 1.0

    # ------------------------------------------------------------------ фаза 2: глубокий анализ
    def _deep_phase(self) -> None:
        p = self.progress
        p.fraction = 0.0
        self._flush_writer()
        rd = self.db.connect()
        try:
            p.deep_total = rd.execute("SELECT COUNT(*) FROM files WHERE deep=0 AND access<>?", (ACCESS_CLOUD,)).fetchone()[0]
            p.deep_done = 0
            tls = threading.local()
            st = self.settings
            pool = ThreadPoolExecutor(max_workers=st.deep_threads(), thread_name_prefix="deep")
            last_id = 0
            last_t, last_n, ema = time.time(), 0, 0.0
            try:
                while not self.stop_ev.is_set():
                    page = rd.execute("SELECT id,path,size,ext,category FROM files WHERE deep=0 AND access<>? AND id>? "
                                      "ORDER BY id LIMIT 400", (ACCESS_CLOUD, last_id)).fetchall()
                    if not page:
                        break
                    last_id = page[-1][0]
                    futs = [pool.submit(self._deep_one, tls, row) for row in page]
                    for f in futs:
                        while not f.done():
                            time.sleep(0.05)
                            now = time.time()
                            if now - last_t >= 0.5:
                                inst = (p.deep_done - last_n) / (now - last_t)
                                ema = inst if ema == 0 else ema * 0.6 + inst * 0.4
                                p.speed, last_t, last_n = ema, now, p.deep_done
                                if p.deep_total:
                                    p.fraction = min(1.0, p.deep_done / p.deep_total)
                p.fraction = 1.0 if not self.stop_ev.is_set() else p.fraction
            finally:
                pool.shutdown(wait=True)
        finally:
            rd.close()

    def _deep_one(self, tls: threading.local, row: tuple) -> None:
        if self.stop_ev.is_set():
            return
        fid, path, size, ext, category = row
        st = self.settings
        p = self.progress
        p.current = path
        upd = {"id": fid, "sha256": None, "sig": None, "mismatch": 0, "category": category,
               "access": ACCESS_OK, "text": None}
        try:
            lp = wu.to_long_path(path)
            with open(lp, "rb", buffering=0) as fh:
                head = fh.read(4096)
                sig = ft.detect_signature(head, ext)
                if sig is not None and sig.name == "ZIP":
                    sig = ft.refine_zip(lp, sig)
                if sig is not None:
                    upd["sig"] = sig.name
                    if not ft.ext_matches(sig, ext):
                        upd["mismatch"] = 1
                    if category == "other" and sig.category != "other":
                        upd["category"] = sig.category
                if size > 0 and self._needs_hash(tls, fid, size):
                    fh.seek(0)
                    upd["sha256"] = hashlib.file_digest(fh, "sha256").hexdigest()
            if st.content_index and self.db.has_fts and size > 0:
                text = content_mod.extract_text(lp, ext, size, upd["sig"] or "", st)
                if text and text.strip():
                    upd["text"] = text
        except OSError as exc:
            kind, msg = classify_error(exc)
            upd["access"] = {"access": ACCESS_DENIED, "locked": ACCESS_LOCKED}.get(kind, ACCESS_ERROR)
            self._report_error(path, exc)
        except Exception as exc:
            log.debug("Глубокий анализ %s: %s", path, exc)
            upd["access"] = ACCESS_ERROR
        self._wq.put(("deep", upd))
        with self._lock:
            p.deep_done += 1

    def _needs_hash(self, tls: threading.local, fid: int, size: int) -> bool:
        if self.settings.hash_all_files:
            return True
        if size < self.settings.min_dup_size:
            return False
        conn = getattr(tls, "conn", None)
        if conn is None:
            conn = tls.conn = self.db.connect()
        return conn.execute("SELECT 1 FROM files WHERE size=? AND id<>? LIMIT 1", (size, fid)).fetchone() is not None


def _j(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def resolve_roots(mode: str, settings: Settings, selected: list[str]) -> list[str]:
    """Определяет корни сканирования для режима."""
    if mode == "quick":
        folders = settings.quick_folders or wu.default_quick_folders()
        return [wu.normalize_root(f) for f in folders if os.path.isdir(f)]
    return [wu.normalize_root(r) for r in selected]
