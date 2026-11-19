"""Экспорт результатов поиска в CSV и JSON (потоково, без загрузки всех строк в память)."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from typing import Callable, Optional

from filetypes import CAT_LABELS, F_HIDDEN, F_SYSTEM
from filters import FileFilter
from scanner import ACCESS_TEXT
from search import SearchEngine

FIELDS = ["name", "extension", "path", "size_bytes", "created", "modified", "type", "signature",
          "hidden", "system", "availability", "sha256"]


def _iso(ts) -> str:
    try:
        return datetime.fromtimestamp(ts).isoformat(timespec="seconds") if ts else ""
    except (OverflowError, OSError, ValueError):
        return ""


def row_to_dict(r: tuple) -> dict:
    _id, path, name, ext, size, ctime, mtime, cat, flags, access, sha, sig, mismatch = r
    return {"name": name, "extension": ext, "path": path, "size_bytes": size, "created": _iso(ctime),
            "modified": _iso(mtime), "type": CAT_LABELS.get(cat, cat), "signature": sig or "",
            "hidden": bool(flags & F_HIDDEN), "system": bool(flags & F_SYSTEM),
            "availability": ACCESS_TEXT.get(access, "?"), "sha256": sha or ""}


def export_results(engine: SearchEngine, flt: FileFilter, sort: str, desc: bool, path: str, fmt: str,
                   progress: Optional[Callable[[int], None]] = None,
                   should_stop: Optional[Callable[[], bool]] = None) -> int:
    """Записывает результаты в файл. fmt: 'csv' | 'json'. Возвращает число строк."""
    n = 0
    if fmt == "csv":
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, delimiter=";")
            w.writeheader()
            for r in engine.iterate(flt, sort, desc):
                if should_stop and should_stop():
                    break
                w.writerow(row_to_dict(r))
                n += 1
                if progress and n % 5000 == 0:
                    progress(n)
    elif fmt == "json":
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[\n")
            first = True
            for r in engine.iterate(flt, sort, desc):
                if should_stop and should_stop():
                    break
                fh.write(("" if first else ",\n") + json.dumps(row_to_dict(r), ensure_ascii=False))
                first = False
                n += 1
                if progress and n % 5000 == 0:
                    progress(n)
            fh.write("\n]\n")
    else:
        raise ValueError(f"Неизвестный формат экспорта: {fmt}")
    return n
