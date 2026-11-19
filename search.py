"""Поисковая система: разбор строки запроса, выполнение поиска, постраничная выдача и экспорт."""
from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Iterator, Optional

from database import Database
from filetypes import F_HIDDEN, F_LINK, F_NOEXT, F_SYSTEM, F_TEMP, F_CLOUD
from filters import (ALL_TYPES, TYPE_ALIASES, FileFilter, FilterError, parse_date_range, parse_size_range)

COLUMNS = ("f.id, f.path, f.name, f.ext, f.size, f.ctime, f.mtime, f.category, f.flags, f.access, "
           "f.sha256, f.sig, f.mismatch")

SORT_KEYS = {
    "name": "f.name_lc", "ext": "f.ext, f.name_lc", "path": "f.path", "size": "f.size",
    "ctime": "f.ctime", "mtime": "f.mtime", "type": "f.category, f.ext, f.name_lc",
    "relevance": "f.name_lc",
}

_KEY_ALIASES = {
    "name": "name", "имя": "name",
    "ext": "ext", "расширение": "ext", "расш": "ext",
    "type": "type", "тип": "type",
    "size": "size", "размер": "size",
    "created": "created", "создан": "created", "создано": "created", "cdate": "created",
    "modified": "modified", "изменен": "modified", "изменён": "modified", "изменено": "modified",
    "mdate": "modified", "date": "modified", "дата": "modified",
    "path": "path", "путь": "path",
    "content": "content", "text": "content", "текст": "content", "содержимое": "content",
    "re": "regex", "regex": "regex", "regexp": "regex", "рег": "regex",
    "hidden": "hidden", "скрытый": "hidden", "скрытые": "hidden",
    "system": "system", "системный": "system", "системные": "system",
    "temp": "temp", "временный": "temp", "временные": "temp",
    "noext": "noext", "безрасширения": "noext",
    "link": "link", "ссылка": "link",
    "cloud": "cloud", "облако": "cloud",
    "access": "access", "доступ": "access", "доступность": "access",
    "dup": "dup", "дубликаты": "dup", "dupes": "dup",
    "mismatch": "mismatch", "несовпадение": "mismatch",
}
_TRUE = {"yes", "true", "1", "да", "y"}
_FALSE = {"no", "false", "0", "нет", "n"}
_TOKEN_RE = re.compile(r'(-?)(?:([^\s:"]+):)?(?:"([^"]*)"|(\S+))')

SYNTAX_HELP = """Синтаксис поиска (слова через пробел — условие «И»):
  отчёт 2024            — имя содержит оба слова
  *.pdf  report??.*     — шаблоны * и ?
  ext:pdf,docx          — расширение (ext:- — без расширения)
  type:image|video|audio|document|archive|app|code|system|temp|noext|hidden
  size:>10mb  size:1mb..5mb  size:<=500kb
  modified:>2024-01-01  modified:2024-05  modified:7d  created:today  created:01.05.2024..31.05.2024
  path:"program files"  или просто фрагмент с «\\»: users\\admin
  content:договор       — слова внутри документов (нужно глубокое сканирование)
  re:^img_\\d+          — регулярное выражение по имени
  hidden:yes  system:yes  temp:yes  noext:yes  access:no  mismatch:yes  dup:yes
  -слово  -ext:tmp  -type:system — исключение"""


def _bool(val: str, key: str) -> bool:
    v = val.lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise FilterError(f"Для «{key}:» укажите yes/no (да/нет)")


def parse_query(text: str) -> FileFilter:
    """Преобразует строку поиска в FileFilter. Некорректный синтаксис -> FilterError."""
    f = FileFilter()
    for m in _TOKEN_RE.finditer(text or ""):
        neg = bool(m.group(1))
        key_raw = (m.group(2) or "").lower()
        val = m.group(3) if m.group(3) is not None else m.group(4)
        if val is None:
            continue
        key = _KEY_ALIASES.get(key_raw) if key_raw else None
        if key_raw and key is None:       # «c:\users» — не ключ, а часть пути/слова
            val = f"{m.group(2)}:{val}"
            key_raw = ""
        if key is None:
            low = val.lower()
            if not low:
                continue
            if "\\" in low or "/" in low:
                (f.not_paths if neg else f.paths).append(low.replace("/", "\\"))
            else:
                (f.not_terms if neg else f.terms).append(low)
            continue
        low = val.lower()
        if key == "name":
            (f.not_terms if neg else f.terms).append(low)
        elif key == "ext":
            vals = [("" if e in ("-", "none", "нет") else e.lstrip(".*")) for e in re.split(r"[,;|\s]+", low) if e]
            (f.not_exts if neg else f.exts).extend(vals)
        elif key == "type":
            for t in re.split(r"[,;|]+", low):
                t = TYPE_ALIASES.get(t.strip(), t.strip())
                if t not in ALL_TYPES:
                    raise FilterError(f"Неизвестный тип «{t}». Доступно: документы, изображения, видео, аудио, "
                                      f"архивы, приложения, код, системные, временные, без_расширения, скрытые")
                (f.not_types if neg else f.types).append(t)
        elif key == "size":
            lo, hi = parse_size_range(low)
            if neg:
                raise FilterError("Исключение по размеру не поддерживается: используйте size:<N или size:>N")
            f.size_min = lo if lo is not None else f.size_min
            f.size_max = hi if hi is not None else f.size_max
        elif key in ("created", "modified"):
            lo, hi = parse_date_range(low)
            if neg:
                raise FilterError("Исключение по дате не поддерживается")
            pre = "ctime" if key == "created" else "mtime"
            if lo is not None:
                setattr(f, f"{pre}_min", lo)
            if hi is not None:
                setattr(f, f"{pre}_max", hi)
        elif key == "path":
            (f.not_paths if neg else f.paths).append(low.replace("/", "\\"))
        elif key == "content":
            f.content = (f.content + " " + val).strip()
        elif key == "regex":
            try:
                re.compile(val)
            except re.error as exc:
                raise FilterError(f"Некорректное регулярное выражение: {exc}") from None
            f.regex = val
        elif key in ("hidden", "system", "temp", "noext", "link", "cloud"):
            mask = {"hidden": F_HIDDEN, "system": F_SYSTEM, "temp": F_TEMP, "noext": F_NOEXT,
                    "link": F_LINK, "cloud": F_CLOUD}[key]
            f.flag_conds[mask] = _bool(val, key_raw) != neg
        elif key == "access":
            f.access_bad = not _bool(val, key_raw) != neg      # access:no -> недоступные
        elif key == "mismatch":
            f.mismatch = _bool(val, key_raw) != neg
        elif key == "dup":
            f.duplicates = _bool(val, key_raw) != neg
    return f


# ---------------------------------------------------------------------------------
_DUP_CTE = ("WITH d AS (SELECT sha256, COUNT(*) AS c FROM files WHERE sha256 IS NOT NULL "
            "GROUP BY sha256 HAVING COUNT(*) > 1) ")


def _build(flt: FileFilter, sort: str, desc: bool) -> tuple[str, str, list]:
    """-> (префикс WITH, 'FROM … WHERE …', params + ORDER BY отдельно)."""
    where, params = flt.where()
    direction = "DESC" if desc else "ASC"
    if flt.duplicates:
        order = "ORDER BY f.size * (d.c - 1) DESC, f.sha256, f.path"
        frm = "FROM files f JOIN d ON d.sha256 = f.sha256"
        return _DUP_CTE, f"{frm} WHERE {where}", params, order
    cols = SORT_KEYS.get(sort, "f.name_lc")
    order = "ORDER BY " + ", ".join(f"{c.strip()} {direction}" for c in cols.split(",")) + ", f.id"
    return "", f"FROM files f WHERE {where}", params, order


class SearchResult:
    """Результат поиска: упорядоченные id во временной таблице SQLite, строки подгружаются страницами."""

    def __init__(self, conn: sqlite3.Connection, count: int, total_size: int):
        self._conn = conn
        self.count = count
        self.total_size = total_size
        self._lock = threading.Lock()
        self._closed = False

    def fetch(self, start: int, limit: int) -> list[tuple]:
        if self._closed or start >= self.count:
            return []
        with self._lock:
            if self._closed:
                return []
            return self._conn.execute(
                f"SELECT {COLUMNS} FROM res r JOIN files f ON f.id = r.id WHERE r.n > ? AND r.n <= ? ORDER BY r.n",
                (start, start + limit)).fetchall()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass


class SearchEngine:
    def __init__(self, db: Database):
        self.db = db

    def run(self, flt: FileFilter, sort: str = "name", desc: bool = False,
            holder: Optional[list] = None) -> SearchResult:
        """Выполняет поиск. `holder` — список, в который кладётся соединение, чтобы его можно было прервать."""
        conn = self.db.connect()
        if holder is not None:
            holder.append(conn)
        try:
            pre, body, params, order = _build(flt, sort, desc)
            conn.execute("CREATE TEMP TABLE res(n INTEGER PRIMARY KEY, id INTEGER NOT NULL)")
            conn.execute(f"{pre}INSERT INTO res(id) SELECT f.id {body} {order}", params)
            count = conn.execute("SELECT COUNT(*) FROM res").fetchone()[0]
            total = conn.execute("SELECT COALESCE(SUM(f.size),0) FROM res r JOIN files f ON f.id=r.id").fetchone()[0] \
                if count else 0
            return SearchResult(conn, count, total)
        except Exception:
            conn.close()
            raise

    def iterate(self, flt: FileFilter, sort: str = "name", desc: bool = False, chunk: int = 2000) -> Iterator[tuple]:
        """Потоковый обход всех результатов (для экспорта) без загрузки в память."""
        conn = self.db.connect()
        try:
            pre, body, params, order = _build(flt, sort, desc)
            cur = conn.execute(f"{pre}SELECT {COLUMNS} {body} {order}", params)
            while True:
                rows = cur.fetchmany(chunk)
                if not rows:
                    return
                yield from rows
        finally:
            conn.close()

    def count(self, flt: FileFilter) -> int:
        conn = self.db.connect()
        try:
            pre, body, params, _ = _build(flt, "name", False)
            return conn.execute(f"{pre}SELECT COUNT(*) {body}", params).fetchone()[0]
        finally:
            conn.close()
