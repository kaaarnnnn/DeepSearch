"""Действия с файлами. Программа ничего не удаляет, не переименовывает и не перемещает."""
from __future__ import annotations

import os
import shutil
import threading
from typing import Callable, Optional

from PySide6.QtCore import QByteArray, QMimeData, QUrl
from PySide6.QtGui import QGuiApplication

import winutils as wu


def copy_paths_to_clipboard(paths: list[str]) -> None:
    QGuiApplication.clipboard().setText("\n".join(paths))


def copy_files_to_clipboard(paths: list[str]) -> None:
    """Кладёт файлы в буфер обмена как «Копировать» в Проводнике (можно вставить в любую папку)."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    mime.setData("Preferred DropEffect", QByteArray(b"\x01\x00\x00\x00"))     # 1 = DROPEFFECT_COPY
    QGuiApplication.clipboard().setMimeData(mime)


def unique_target(dest_dir: str, name: str) -> str:
    base, ext = os.path.splitext(name)
    target = os.path.join(dest_dir, name)
    i = 2
    while os.path.exists(target):
        target = os.path.join(dest_dir, f"{base} (копия {i}){ext}")
        i += 1
    return target


def copy_files_to_folder(paths: list[str], dest_dir: str, progress: Optional[Callable[[str, int, int], None]] = None,
                         stop: Optional[threading.Event] = None) -> tuple[int, list[str]]:
    """Копирует файлы (исходные не изменяются, существующие файлы не перезаписываются)."""
    done, errors = 0, []
    for i, src in enumerate(paths):
        if stop is not None and stop.is_set():
            break
        if progress:
            progress(os.path.basename(src), i, len(paths))
        try:
            shutil.copy2(wu.to_long_path(src), wu.to_long_path(unique_target(dest_dir, os.path.basename(src))))
            done += 1
        except OSError as exc:
            errors.append(f"{src}: {exc}")
    return done, errors
