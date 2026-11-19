"""Диалоги: свойства файла, ошибки доступа, выбор дисков."""
from __future__ import annotations

import csv
import hashlib
import os
from datetime import datetime
from typing import Callable, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QScrollArea, QTableView, QVBoxLayout, QWidget, QHeaderView)

import filetypes as ft
import winutils as wu
from database import Database
from scanner import ACCESS_TEXT, ERROR_KIND_TEXT
from ui import actions
from ui.models import R_ACCESS, R_CAT, R_CTIME, R_EXT, R_FLAGS, R_MISMATCH, R_MTIME, R_NAME, R_PATH, R_SHA, R_SIG, R_SIZE
from ui.models import attrs_text, type_text
from ui.theme import palette
from ui.widgets import AnimButton, TaskThread, UsageBar
from utils import fmt_int, fmt_ts, human_size


class PropertiesDialog(QDialog):
    def __init__(self, row: tuple, parent=None):
        super().__init__(parent)
        self.row = row
        self.setWindowTitle(f"Свойства — {row[R_NAME]}")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        head = QLabel(row[R_NAME])
        head.setObjectName("h2")
        head.setWordWrap(True)
        head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self._grid, self._r = grid, 0
        path = row[R_PATH]
        live = self._live_state(path)
        self._add("Полный путь", path)
        self._add("Формат", row[R_EXT].upper() if row[R_EXT] else "нет расширения")
        self._add("Тип файла", type_text(row))
        self._add("Размер", f"{human_size(row[R_SIZE])}  ({fmt_int(row[R_SIZE])} байт)")
        self._add("Создан", fmt_ts(row[R_CTIME]))
        self._add("Изменён", fmt_ts(row[R_MTIME]))
        self._add("Атрибуты", attrs_text(row[R_FLAGS]))
        self._add("Доступность в индексе", ACCESS_TEXT.get(row[R_ACCESS], "?"))
        self._add("Доступность сейчас", live)
        self.sha_label = self._add("SHA-256", row[R_SHA] or "не вычислен")
        lay.addLayout(grid)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        lay.addWidget(self.status)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        for text, icon, fn in (("Открыть", "open", lambda: self._safe(wu.open_path, path)),
                               ("Показать в папке", "location", lambda: self._safe(wu.reveal_in_explorer, path)),
                               ("Копировать путь", "copy", lambda: actions.copy_paths_to_clipboard([path]))):
            b = AnimButton(text, icon)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        self.sha_btn = AnimButton("Вычислить SHA-256", "shield")
        self.sha_btn.clicked.connect(self._calc_hash)
        self.sha_btn.setVisible(not row[R_SHA])
        btns.addWidget(self.sha_btn)
        if wu.IS_WIN:
            b = AnimButton("Свойства Windows", "info")
            b.clicked.connect(lambda: wu.show_native_properties(path))
            btns.addWidget(b)
        lay.addLayout(btns)

    def _add(self, key: str, value: str) -> QLabel:
        k = QLabel(key)
        k.setObjectName("muted")
        k.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        v = QLabel(value)
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._grid.addWidget(k, self._r, 0)
        self._grid.addWidget(v, self._r, 1)
        self._r += 1
        return v

    @staticmethod
    def _live_state(path: str) -> str:
        lp = wu.to_long_path(path)
        try:
            st = os.stat(lp)
        except FileNotFoundError:
            return "Файл не найден (удалён, перемещён или диск отключён)"
        except PermissionError:
            return "Нет доступа к файлу"
        except OSError as exc:
            return f"Ошибка: {exc}"
        try:
            with open(lp, "rb"):
                pass
        except PermissionError:
            return "Нет прав на чтение"
        except OSError as exc:
            return f"Файл заблокирован или недоступен ({exc})"
        return "Доступен для чтения"

    def _safe(self, fn: Callable, path: str):
        try:
            fn(path)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Не удалось выполнить действие: {exc}")

    def _calc_hash(self):
        path = self.row[R_PATH]
        self.sha_btn.setEnabled(False)
        self.sha_label.setText("вычисляется…")

        def work(progress, stop):
            with open(wu.to_long_path(path), "rb", buffering=0) as fh:
                return hashlib.file_digest(fh, "sha256").hexdigest()
        self._t = TaskThread(work, self)
        self._t.done.connect(lambda h: self.sha_label.setText(h))
        self._t.failed.connect(lambda m: (self.sha_label.setText("не удалось вычислить"), self.status.setText(m)))
        self._t.start()

    def done_cleanup(self):
        if getattr(self, "_t", None) and self._t.isRunning():
            self._t.stop_event.set()
            self._t.wait(2000)

    def closeEvent(self, e):
        self.done_cleanup()
        super().closeEvent(e)


class _ErrModel(QAbstractTableModel):
    HEAD = ["Путь", "Причина", "Описание"]

    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def rowCount(self, p=QModelIndex()):
        return 0 if p.isValid() else len(self.rows)

    def columnCount(self, p=QModelIndex()):
        return 3

    def headerData(self, s, o, role=Qt.DisplayRole):
        return self.HEAD[s] if o == Qt.Horizontal and role == Qt.DisplayRole else None

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        path, kind, msg, ts = self.rows[idx.row()]
        if role == Qt.DisplayRole:
            return (path, ERROR_KIND_TEXT.get(kind, kind), msg)[idx.column()]
        if role == Qt.ToolTipRole:
            return f"{path}\n{msg}\n{fmt_ts(ts)}"
        return None


class ErrorsDialog(QDialog):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Что не удалось просканировать")
        self.resize(980, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        self.summary = QLabel()
        self.summary.setObjectName("h2")
        lay.addWidget(self.summary)
        note = QLabel("DeepSearch не обходит защиту Windows: к этим объектам у текущего пользователя нет доступа, они "
                      "заняты другой программой или недоступны. Для расширения доступа можно запустить программу от "
                      "имени администратора — защита системы при этом не отключается.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        top = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItem("Все причины", None)
        for k, t in ERROR_KIND_TEXT.items():
            self.kind.addItem(t, k)
        self.kind.currentIndexChanged.connect(self.reload)
        top.addWidget(self.kind)
        top.addStretch(1)
        exp = AnimButton("Экспорт в CSV", "export")
        exp.clicked.connect(self._export)
        top.addWidget(exp)
        lay.addLayout(top)
        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.reload()

    def reload(self):
        summ = self.db.error_summary()
        total = sum(summ.values())
        parts = [f"{ERROR_KIND_TEXT.get(k, k)}: {fmt_int(v)}" for k, v in sorted(summ.items(), key=lambda x: -x[1])]
        self.summary.setText(f"Проблемных объектов: {fmt_int(total)}" + (f"  ·  {';  '.join(parts)}" if parts else ""))
        rows = self.db.errors(self.kind.currentData(), limit=100000)
        self.table.setModel(_ErrModel(rows))
        self.table.setColumnWidth(0, 520)
        self.table.setColumnWidth(1, 200)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт ошибок", "deepsearch-errors.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(["path", "reason", "message", "time"])
                for p, k, m, ts in self.db.errors(self.kind.currentData(), limit=10 ** 7):
                    w.writerow([p, ERROR_KIND_TEXT.get(k, k), m, datetime.fromtimestamp(ts).isoformat(timespec="seconds")])
        except OSError as exc:
            QMessageBox.warning(self, "Экспорт", f"Не удалось сохранить файл:\n{exc}")


class DrivePopup(QFrame):
    """Всплывающее окно выбора дисков и папок для сканирования."""
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("drivePopup")
        self.settings = settings
        self.setMinimumWidth(430)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 12, 14, 12)
        self.lay.setSpacing(8)
        self.rows_host = QVBoxLayout()
        self.rows_host.setSpacing(8)
        title = QLabel("Что сканировать")
        title.setObjectName("h2")
        self.lay.addWidget(title)
        self.lay.addLayout(self.rows_host)
        bar = QHBoxLayout()
        self.all_btn = AnimButton("Выбрать все диски", "drive", "ghost")
        self.all_btn.clicked.connect(self._select_all)
        self.add_btn = AnimButton("Добавить папку…", "folder", "ghost")
        self.add_btn.clicked.connect(self._add_folder)
        bar.addWidget(self.all_btn)
        bar.addWidget(self.add_btn)
        self.lay.addLayout(bar)
        self.drives: list[wu.DriveInfo] = []
        self._boxes: list[tuple[QCheckBox, str]] = []

    def set_drives(self, drives: list[wu.DriveInfo]) -> None:
        self.drives = drives
        self._rebuild()

    def _rebuild(self):
        while self.rows_host.count():
            it = self.rows_host.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._boxes = []
        sel = set(r.lower() for r in self.settings.scan_roots)
        for d in self.drives:
            self._add_row(d.root, f"{d.root.rstrip(chr(92))}  {d.title}" + (f" · {d.fs}" if d.fs else ""),
                          (f"{human_size(d.free)} свободно из {human_size(d.total)}" if d.ready else "Нет носителя"),
                          (d.used / d.total) if d.total else 0, d.ready, d.root.lower() in sel, d.kind == "removable")
        for f in self.settings.custom_folders:
            self._add_row(f, f, "Папка", 0, os.path.isdir(f), f.lower() in sel, False, removable_entry=True)

    def _add_row(self, root, title, sub, frac, enabled, checked, removable, removable_entry=False):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        h = QHBoxLayout()
        cb = QCheckBox(title + ("  (съёмный)" if removable else ""))
        cb.setChecked(checked and enabled)
        cb.setEnabled(enabled)
        cb.toggled.connect(lambda _=None: self._emit())
        h.addWidget(cb, 1)
        if removable_entry:
            x = AnimButton("", "stop", "ghost")
            x.setFixedSize(28, 28)
            x.setToolTip("Убрать из списка")
            x.clicked.connect(lambda: self._remove_folder(root))
            h.addWidget(x)
        v.addLayout(h)
        sl = QLabel(sub)
        sl.setObjectName("muted")
        sl.setContentsMargins(24, 0, 0, 0)
        v.addWidget(sl)
        if frac:
            bar = UsageBar()
            bar.fraction = frac
            v.addWidget(bar)
        self.rows_host.addWidget(w)
        self._boxes.append((cb, root))

    def selected(self) -> list[str]:
        return [r for cb, r in self._boxes if cb.isChecked()]

    def _emit(self):
        self.settings.scan_roots = self.selected()
        self.settings.save()
        self.changed.emit()

    def _select_all(self):
        for cb, root in self._boxes:
            if cb.isEnabled():
                cb.setChecked(True)

    def _add_folder(self):
        self.hide()
        path = QFileDialog.getExistingDirectory(self.parent(), "Выберите папку для сканирования")
        if path:
            path = wu.normalize_root(path)
            if path not in self.settings.custom_folders:
                self.settings.custom_folders.append(path)
            if path not in self.settings.scan_roots:
                self.settings.scan_roots.append(path)
            self.settings.save()
            self._rebuild()
            self.changed.emit()

    def _remove_folder(self, root):
        self.settings.custom_folders = [f for f in self.settings.custom_folders if f != root]
        self.settings.scan_roots = [f for f in self.settings.scan_roots if f != root]
        self.settings.save()
        self._rebuild()
        self.changed.emit()
