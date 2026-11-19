"""Настройки приложения (JSON в каталоге данных пользователя)."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from utils import app_data_dir

log = logging.getLogger("deepsearch.settings")

DEFAULT_TEXT_EXTS = [
    "txt", "md", "markdown", "rst", "log", "csv", "tsv", "json", "xml", "yaml", "yml", "toml", "ini", "cfg",
    "conf", "properties", "html", "htm", "xhtml", "css", "scss", "less", "js", "mjs", "ts", "tsx", "jsx", "vue",
    "py", "pyw", "c", "h", "cpp", "cc", "cxx", "hpp", "cs", "java", "kt", "go", "rs", "rb", "php", "swift",
    "lua", "sql", "sh", "bat", "cmd", "ps1", "vbs", "tex", "srt", "ass", "reg", "inf", "svg", "bib", "r", "pl",
    "dart", "asm", "gradle", "cmake", "makefile", "gitignore", "env", "nfo", "diz",
]


@dataclass
class Settings:
    theme: str = "dark"                       # dark / light
    animations: bool = True
    threads: int = 0                          # 0 = авто
    quick_folders: list = field(default_factory=list)   # пусто = автоопределение
    exclude_names: list = field(default_factory=list)   # имена каталогов, пропускаемых во всех режимах
    exclude_paths: list = field(default_factory=list)   # полные пути, пропускаемые во всех режимах
    quick_exclude_names: list = field(default_factory=lambda: [
        "node_modules", ".git", "__pycache__", ".venv", "venv", ".cache", "$recycle.bin"])
    quick_skip_hidden_dirs: bool = True
    hash_all_files: bool = False              # False: SHA-256 только у файлов с совпадающим размером
    content_index: bool = True
    content_max_kb: int = 256                 # сколько текста на файл попадает в индекс
    content_max_file_mb: int = 64             # макс. размер контейнера (docx/pdf...) для разбора
    text_exts: list = field(default_factory=lambda: list(DEFAULT_TEXT_EXTS))
    large_file_mb: int = 100
    min_dup_size: int = 1
    history_limit: int = 200
    auto_update_on_start: bool = False
    scan_roots: list = field(default_factory=list)
    custom_folders: list = field(default_factory=list)
    scan_mode: str = "full"

    # ------------------------------------------------------------------
    @staticmethod
    def path() -> Path:
        return app_data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in known:
                    setattr(s, k, v)
        except FileNotFoundError:
            pass
        except Exception as exc:  # повреждённый файл не должен мешать запуску
            log.warning("Не удалось прочитать настройки: %s", exc)
        return s

    def save(self) -> None:
        p = self.path()
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, p)
        except OSError as exc:
            log.error("Не удалось сохранить настройки: %s", exc)

    # ------------------------------------------------------------------
    def worker_threads(self) -> int:
        if self.threads and self.threads > 0:
            return max(1, min(64, self.threads))
        # Измерения показали: при тёплом кэше лишние потоки упираются в GIL; 4 — разумный компромисс.
        return max(2, min(4, os.cpu_count() or 2))

    def deep_threads(self) -> int:
        return max(2, min(8, os.cpu_count() or 4))
