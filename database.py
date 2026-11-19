"""SQLite-индекс файлов: схема, соединения, история, ошибки, состояние сканирования, статистика."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from utils import app_data_dir

log = logging.getLogger("deepsearch.db")

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS dirs(id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS files(
    id       INTEGER PRIMARY KEY,
    path     TEXT NOT NULL UNIQUE,
    dir_id   INTEGER NOT NULL,
    name     TEXT NOT NULL,
    name_lc  TEXT NOT NULL,
    ext      TEXT NOT NULL,
    size     INTEGER NOT NULL,
    ctime    INTEGER,
    mtime    INTEGER,
    category TEXT NOT NULL,
    flags    INTEGER NOT NULL DEFAULT 0,
    access   INTEGER NOT NULL DEFAULT 1,
    gen      INTEGER NOT NULL DEFAULT 0,
    deep     INTEGER NOT NULL DEFAULT 0,
    sha256   TEXT,
    sig      TEXT,
    mismatch INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_files_dir   ON files(dir_id);
CREATE INDEX IF NOT EXISTS ix_files_name  ON files(name_lc);
CREATE INDEX IF NOT EXISTS ix_files_ext   ON files(ext);
CREATE INDEX IF NOT EXISTS ix_files_size  ON files(size);
CREATE INDEX IF NOT EXISTS ix_files_mtime ON files(mtime);
CREATE INDEX IF NOT EXISTS ix_files_cat   ON files(category);
CREATE INDEX IF NOT EXISTS ix_files_sha   ON files(sha256) WHERE sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_files_deep  ON files(deep) WHERE deep = 0;
CREATE TABLE IF NOT EXISTS errors(
    id INTEGER PRIMARY KEY, path TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS history(
    id INTEGER PRIMARY KEY, query TEXT NOT NULL UNIQUE, ts INTEGER NOT NULL, hits INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS session(
    id INTEGER PRIMARY KEY CHECK (id = 1), mode TEXT, roots TEXT, gen INTEGER, status TEXT, phase TEXT,
    started INTEGER, finished INTEGER, stats TEXT);
CREATE TABLE IF NOT EXISTS pending_dirs(path TEXT PRIMARY KEY);
"""

FTS_SCHEMA = ("CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5("
              "text, tokenize='unicode61 remove_diacritics 2', columnsize=0)")


@lru_cache(maxsize=256)
def _compile(pattern: str):
    return re.compile(pattern, re.IGNORECASE)


def _regexp(pattern: str, value: Optional[str]) -> int:
    if value is None:
        return 0
    try:
        return 1 if _compile(pattern).search(value) else 0
    except re.error:
        return 0


def _ulower(value: Optional[str]) -> Optional[str]:
    return value.lower() if value is not None else None


class Database:
    def __init__(self, path: Optional[str | Path] = None):
        self.path = str(path or (app_data_dir() / "index.db"))
        self.has_fts = False
        self._init_schema()

    # ------------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=60, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA cache_size=-65536")
        conn.create_function("ulower", 1, _ulower, deterministic=True)
        conn.create_function("regexp", 2, _regexp, deterministic=True)
        return conn

    def _init_schema(self) -> None:
        conn = self.connect()
        try:
            conn.executescript(SCHEMA)
            try:
                conn.execute(FTS_SCHEMA)
                self.has_fts = True
            except sqlite3.Error as exc:
                log.warning("FTS5 недоступен, поиск по содержимому отключён: %s", exc)
                self.has_fts = False
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            if ver < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        finally:
            conn.close()

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str, default=None):
        conn = self.connect()
        try:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default
        finally:
            conn.close()

    def set_meta(self, key: str, value, conn: Optional[sqlite3.Connection] = None) -> None:
        own = conn is None
        conn = conn or self.connect()
        try:
            conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps(value, ensure_ascii=False)))
        finally:
            if own:
                conn.close()

    # --------------------------------------------------------------- session
    def get_session(self) -> Optional[dict]:
        conn = self.connect()
        try:
            r = conn.execute("SELECT mode,roots,gen,status,phase,started,finished,stats FROM session WHERE id=1").fetchone()
            if not r:
                return None
            return {"mode": r[0], "roots": json.loads(r[1] or "[]"), "gen": r[2], "status": r[3], "phase": r[4],
                    "started": r[5], "finished": r[6], "stats": json.loads(r[7] or "{}")}
        finally:
            conn.close()

    def pending_dirs(self) -> list[str]:
        conn = self.connect()
        try:
            return [r[0] for r in conn.execute("SELECT path FROM pending_dirs")]
        finally:
            conn.close()

    def can_resume(self) -> bool:
        s = self.get_session()
        if not s or s["status"] not in ("interrupted", "running"):
            return False
        if s["phase"] == "deep":
            return True
        conn = self.connect()
        try:
            return conn.execute("SELECT 1 FROM pending_dirs LIMIT 1").fetchone() is not None
        finally:
            conn.close()

    def has_files(self) -> bool:
        conn = self.connect()
        try:
            return conn.execute("SELECT 1 FROM files LIMIT 1").fetchone() is not None
        finally:
            conn.close()

    # --------------------------------------------------------------- history
    def add_history(self, query: str, hits: int, limit: int = 200) -> None:
        query = query.strip()
        if not query:
            return
        conn = self.connect()
        try:
            conn.execute("INSERT INTO history(query,ts,hits) VALUES(?,?,?) "
                         "ON CONFLICT(query) DO UPDATE SET ts=excluded.ts, hits=excluded.hits",
                         (query, int(time.time()), hits))
            conn.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY ts DESC LIMIT ?)", (limit,))
        finally:
            conn.close()

    def history(self, limit: int = 50) -> list[tuple[str, int, int]]:
        conn = self.connect()
        try:
            return conn.execute("SELECT query, ts, hits FROM history ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()

    def clear_history(self) -> None:
        conn = self.connect()
        try:
            conn.execute("DELETE FROM history")
        finally:
            conn.close()

    # ---------------------------------------------------------------- errors
    def errors(self, kind: Optional[str] = None, limit: int = 50000) -> list[tuple]:
        conn = self.connect()
        try:
            if kind:
                return conn.execute("SELECT path,kind,message,ts FROM errors WHERE kind=? ORDER BY id LIMIT ?",
                                    (kind, limit)).fetchall()
            return conn.execute("SELECT path,kind,message,ts FROM errors ORDER BY id LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()

    def error_summary(self) -> dict[str, int]:
        conn = self.connect()
        try:
            return dict(conn.execute("SELECT kind, COUNT(*) FROM errors GROUP BY kind").fetchall())
        finally:
            conn.close()

    # ----------------------------------------------------------------- stats
    def totals(self, full: bool = False) -> dict:
        """Общие числа индекса. full=True добавляет число скрытых файлов (полный проход по таблице)."""
        conn = self.connect()
        try:
            n, size = conn.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM files").fetchone()
            dirs = conn.execute("SELECT COUNT(*) FROM dirs").fetchone()[0]
            errs = conn.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
            hidden = conn.execute("SELECT COUNT(*) FROM files WHERE flags & 1").fetchone()[0] if full else 0
            return {"files": n, "size": size, "dirs": dirs, "errors": errs, "hidden": hidden}
        finally:
            conn.close()

    def stats(self) -> dict:
        """Полная статистика для главной страницы (выполняется в фоновом потоке)."""
        conn = self.connect()
        try:
            out = self.totals(full=True)
            out["by_category"] = conn.execute(
                "SELECT category, COUNT(*), COALESCE(SUM(size),0) FROM files GROUP BY category").fetchall()
            out["top_ext"] = conn.execute(
                "SELECT ext, COUNT(*), COALESCE(SUM(size),0) FROM files GROUP BY ext ORDER BY 2 DESC LIMIT 12").fetchall()
            out["largest"] = conn.execute(
                "SELECT path, size FROM files ORDER BY size DESC LIMIT 10").fetchall()
            out["noext"] = conn.execute("SELECT COUNT(*) FROM files WHERE flags & 16").fetchone()[0]
            return out
        finally:
            conn.close()

    def clear_index(self) -> None:
        conn = self.connect()
        try:
            for t in ("files", "dirs", "errors", "pending_dirs", "session"):
                conn.execute(f"DELETE FROM {t}")
            if self.has_fts:
                conn.execute("DELETE FROM content_fts")
            conn.execute("DELETE FROM meta WHERE key IN ('last_roots','last_mode')")
            conn.execute("VACUUM")
        finally:
            conn.close()


# ----------------------------------------------------------------------
# SQL-операторы, используемые писателем сканера
# ----------------------------------------------------------------------
UPSERT_FILE = """
INSERT INTO files(path,dir_id,name,name_lc,ext,size,ctime,mtime,category,flags,access,gen)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(path) DO UPDATE SET
    dir_id=excluded.dir_id,
    size=excluded.size, ctime=excluded.ctime, mtime=excluded.mtime,
    category=CASE WHEN files.deep=1 AND files.size=excluded.size AND files.mtime=excluded.mtime
                  THEN files.category ELSE excluded.category END,
    flags=excluded.flags, access=excluded.access, gen=excluded.gen,
    sha256=CASE WHEN files.size=excluded.size AND files.mtime=excluded.mtime THEN files.sha256 END,
    sig=CASE WHEN files.size=excluded.size AND files.mtime=excluded.mtime THEN files.sig END,
    mismatch=CASE WHEN files.size=excluded.size AND files.mtime=excluded.mtime THEN files.mismatch ELSE 0 END,
    deep=CASE WHEN files.size=excluded.size AND files.mtime=excluded.mtime AND files.access IN (1,3)
              THEN files.deep ELSE 0 END
"""
