"""Общие вспомогательные функции DeepSearch."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

APP_NAME = "DeepSearch"
APP_VERSION = "1.0.0"


def app_data_dir() -> Path:
    """Каталог для индекса, настроек и логов (создаётся при необходимости)."""
    override = os.environ.get("DEEPSEARCH_HOME")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def resource_path(rel: str) -> str:
    """Путь к ресурсу как в исходниках, так и внутри PyInstaller-сборки."""
    root = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, rel)


_UNITS = ("Б", "КБ", "МБ", "ГБ", "ТБ", "ПБ")


def human_size(n: float | int | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    for i, unit in enumerate(_UNITS):
        if n < 1024 or i == len(_UNITS) - 1:
            if i == 0:
                return f"{int(n)} {unit}"
            return f"{n:.1f} {unit}" if n < 100 else f"{n:.0f} {unit}"
        n /= 1024
    return "—"


def fmt_int(n: int | None) -> str:
    if n is None:
        return "0"
    return f"{int(n):,}".replace(",", " ")


def fmt_ts(ts: int | float | None) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")
    except (OverflowError, OSError, ValueError):
        return "—"


def fmt_duration(sec: float) -> str:
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
