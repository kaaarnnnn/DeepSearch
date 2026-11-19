"""Поиск дубликатов: файлы группируются по размеру, затем сравниваются по SHA-256."""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import winutils as wu
from database import Database
from scanner import ACCESS_CLOUD, ACCESS_DENIED, ACCESS_ERROR, ACCESS_LOCKED, classify_error
from settings import Settings

log = logging.getLogger("deepsearch.dups")

ProgressCb = Callable[[int, int, str], None]       # (сделано, всего, текущий файл)


class DuplicateDetector:
    def __init__(self, db: Database, settings: Settings, stop: Optional[threading.Event] = None):
        self.db = db
        self.settings = settings
        self.stop = stop or threading.Event()

    # ------------------------------------------------------------------
    def _candidates_sql(self) -> str:
        return ("FROM files WHERE size >= ? AND size > 0 AND sha256 IS NULL AND access NOT IN (3) "
                "AND size IN (SELECT size FROM files WHERE size >= ? GROUP BY size HAVING COUNT(*) > 1)")

    def pending_hash_count(self) -> int:
        conn = self.db.connect()
        try:
            m = max(1, self.settings.min_dup_size)
            return conn.execute(f"SELECT COUNT(*) {self._candidates_sql()}", (m, m)).fetchone()[0]
        finally:
            conn.close()

    def compute_hashes(self, progress: Optional[ProgressCb] = None) -> tuple[int, int]:
        """Считает SHA-256 для файлов, у которых есть «двойник» по размеру. -> (посчитано, ошибок)."""
        m = max(1, self.settings.min_dup_size)
        rd, wr = self.db.connect(), self.db.connect()
        done = errors = 0
        try:
            total = rd.execute(f"SELECT COUNT(*) {self._candidates_sql()}", (m, m)).fetchone()[0]
            last_id = 0
            pool = ThreadPoolExecutor(max_workers=self.settings.deep_threads(), thread_name_prefix="hash")
            try:
                while not self.stop.is_set():
                    page = rd.execute(f"SELECT id, path {self._candidates_sql()} AND id > ? ORDER BY id LIMIT 200",
                                      (m, m, last_id)).fetchall()
                    if not page:
                        break
                    last_id = page[-1][0]
                    results = list(pool.map(self._hash_one, page))
                    wr.execute("BEGIN")
                    for fid, digest, access in results:
                        if digest is None and access is None:
                            continue                       # остановлено
                        if digest:
                            wr.execute("UPDATE files SET sha256=? WHERE id=?", (digest, fid))
                        else:
                            errors += 1
                            wr.execute("UPDATE files SET access=? WHERE id=?", (access, fid))
                        done += 1
                    wr.execute("COMMIT")
                    if progress:
                        progress(done, total, page[-1][1])
            finally:
                pool.shutdown(wait=True)
        finally:
            rd.close()
            wr.close()
        return done, errors

    def _hash_one(self, row: tuple) -> tuple[int, Optional[str], Optional[int]]:
        fid, path = row
        if self.stop.is_set():
            return fid, None, None
        try:
            with open(wu.to_long_path(path), "rb", buffering=0) as fh:
                return fid, hashlib.file_digest(fh, "sha256").hexdigest(), None
        except OSError as exc:
            kind, _ = classify_error(exc)
            return fid, "", {"access": ACCESS_DENIED, "locked": ACCESS_LOCKED}.get(kind, ACCESS_ERROR)

    # ------------------------------------------------------------------
    def summary(self) -> dict:
        """Сколько групп дубликатов, файлов в них и сколько места занимают лишние копии."""
        conn = self.db.connect()
        try:
            r = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(c),0), COALESCE(SUM(size*(c-1)),0) FROM "
                "(SELECT COUNT(*) c, MAX(size) size FROM files WHERE sha256 IS NOT NULL GROUP BY sha256 HAVING COUNT(*)>1)"
            ).fetchone()
            return {"groups": r[0], "files": r[1], "wasted": r[2]}
        finally:
            conn.close()

    def groups(self, limit: int = 100):
        """Крупнейшие группы дубликатов: (sha256, число копий, размер, [пути])."""
        conn = self.db.connect()
        try:
            out = []
            for sha, c, size in conn.execute(
                    "SELECT sha256, COUNT(*) c, MAX(size) FROM files WHERE sha256 IS NOT NULL GROUP BY sha256 "
                    "HAVING c>1 ORDER BY MAX(size)*(COUNT(*)-1) DESC LIMIT ?", (limit,)).fetchall():
                paths = [r[0] for r in conn.execute("SELECT path FROM files WHERE sha256=? ORDER BY path", (sha,))]
                out.append((sha, c, size, paths))
            return out
        finally:
            conn.close()
