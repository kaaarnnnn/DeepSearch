"""Главная страница: статистика индекса, диаграммы, быстрый запуск сканирования."""
from __future__ import annotations

import os
import time
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea,
                               QVBoxLayout, QWidget)

import filetypes as ft
import winutils as wu
from database import Database
from settings import Settings
from ui.theme import palette
from ui.widgets import AnimButton, BarChart, Card, DonutChart, StatCard, TaskThread, UsageBar
from utils import fmt_duration, fmt_int, human_size

MODE_INFO = {
    "quick": ("Быстрое", "bolt", "Только индексируемые каталоги пользователя: Рабочий стол, Документы, Загрузки, "
                                  "Изображения, Видео, Музыка, OneDrive, меню «Пуск». Максимальная скорость."),
    "full": ("Полное", "drive", "Все выбранные диски и вложенные папки: скрытые и системные файлы, файлы без "
                                "расширений, AppData, ProgramData, внешние накопители."),
    "deep": ("Глубокое", "search", "Полное + определение типа по сигнатуре, SHA-256 для поиска дубликатов и "
                                   "индексация текста внутри документов."),
}


class ModeCard(Card):
    clicked = Signal(str)

    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode
        title, icon, desc = MODE_INFO[mode]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(8)
        t = QLabel(f"{title} сканирование")
        t.setObjectName("h2")
        d = QLabel(desc)
        d.setObjectName("muted")
        d.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(d, 1)
        self.btn = AnimButton("Запустить", "play", "primary" if mode == "full" else "normal")
        self.btn.clicked.connect(lambda: self.clicked.emit(mode))
        lay.addWidget(self.btn, 0, Qt.AlignLeft)


class HomePage(QWidget):
    scanRequested = Signal(str)
    openErrors = Signal()
    openScope = Signal(str, str)         # (раздел, текст запроса)
    revealRequested = Signal(str)
    restartAdmin = Signal()

    def __init__(self, db: Database, settings: Settings, parent=None):
        super().__init__(parent)
        self.db, self.settings = db, settings
        self._thread = None
        self.drives: list = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(14)

        h = QLabel("DeepSearch")
        h.setObjectName("h1")
        sub = QLabel("Глубокий поиск всех файлов на компьютере: быстро, полно и честно о том, что недоступно.")
        sub.setObjectName("muted")
        lay.addWidget(h)
        lay.addWidget(sub)

        self.admin_banner = QFrame()
        self.admin_banner.setObjectName("banner")
        bl = QHBoxLayout(self.admin_banner)
        bl.setContentsMargins(14, 10, 12, 10)
        lbl = QLabel("Программа запущена без прав администратора: часть системных папок будет недоступна. "
                     "Всё, что не удалось прочитать, будет перечислено в отчёте об ошибках.")
        lbl.setWordWrap(True)
        bl.addWidget(lbl, 1)
        ab = AnimButton("Перезапустить от администратора", "shield")
        ab.clicked.connect(self.restartAdmin.emit)
        bl.addWidget(ab)
        self.admin_banner.setVisible(wu.IS_WIN and not wu.is_admin())
        lay.addWidget(self.admin_banner)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.c_files = StatCard("Файлов в индексе", "files", "#4C8DFF")
        self.c_size = StatCard("Общий размер", "drive", "#2BC48A")
        self.c_dirs = StatCard("Папок просканировано", "folder", "#FFB020")
        self.c_err = StatCard("Недоступно / ошибки", "alert", "#FF5C6C", clickable=True)
        self.c_err.setToolTip("Нажмите, чтобы увидеть список")
        self.c_err.clicked.connect(self.openErrors.emit)
        for i, c in enumerate((self.c_files, self.c_size, self.c_dirs, self.c_err)):
            cards.addWidget(c, 0, i)
            cards.setColumnStretch(i, 1)
        lay.addLayout(cards)

        modes = QHBoxLayout()
        modes.setSpacing(12)
        for m in ("quick", "full", "deep"):
            mc = ModeCard(m)
            mc.clicked.connect(self.scanRequested.emit)
            modes.addWidget(mc, 1)
        lay.addLayout(modes)

        charts = QHBoxLayout()
        charts.setSpacing(12)
        c1, c2 = Card(), Card()
        v1, v2 = QVBoxLayout(c1), QVBoxLayout(c2)
        for v, t in ((v1, "Занятое пространство по типам"), (v2, "Распределение по форматам (кол-во файлов)")):
            v.setContentsMargins(18, 14, 18, 14)
            lab = QLabel(t)
            lab.setObjectName("h2")
            v.addWidget(lab)
        self.donut = DonutChart()
        self.donut.segmentClicked.connect(lambda k: self.openScope.emit(k if k in ("documents", "images", "video", "audio", "archives", "apps") else "all", "" if k in ("documents", "images", "video", "audio", "archives", "apps") else f"type:{k}"))
        self.bars = BarChart()
        v1.addWidget(self.donut, 1)
        v2.addWidget(self.bars, 1)
        charts.addWidget(c1, 1)
        charts.addWidget(c2, 1)
        lay.addLayout(charts)

        lower = QHBoxLayout()
        lower.setSpacing(12)
        c3, c4 = Card(), Card()
        v3, v4 = QVBoxLayout(c3), QVBoxLayout(c4)
        for v, t in ((v3, "Диски"), (v4, "Самые большие файлы")):
            v.setContentsMargins(18, 14, 18, 14)
            lab = QLabel(t)
            lab.setObjectName("h2")
            v.addWidget(lab)
        self.drives_box = QVBoxLayout()
        self.drives_box.setSpacing(10)
        v3.addLayout(self.drives_box)
        v3.addStretch(1)
        self.largest = QListWidget()
        self.largest.setMinimumHeight(200)
        self.largest.itemDoubleClicked.connect(lambda it: self.revealRequested.emit(it.data(Qt.UserRole)))
        self.largest.setToolTip("Двойной щелчок — показать в Проводнике")
        v4.addWidget(self.largest, 1)
        lower.addWidget(c3, 1)
        lower.addWidget(c4, 1)
        lay.addLayout(lower)

        self.last = QLabel("")
        self.last.setObjectName("muted")
        lay.addWidget(self.last)
        lay.addStretch(1)

    def refresh(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        t = TaskThread(lambda prog, stop: self.db.stats(), self)
        t.done.connect(self._apply)
        t.failed.connect(lambda m: self.last.setText(f"Не удалось получить статистику: {m}"))
        self._thread = t
        t.start()
        self._fill_drives()

    def set_drives(self, drives: list) -> None:
        self.drives = drives
        self._fill_drives()

    def _fill_drives(self) -> None:
        while self.drives_box.count():
            it = self.drives_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for d in self.drives:
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(3)
            row = QHBoxLayout()
            name = QLabel(f"{d.root.rstrip(chr(92))}  {d.title}")
            info = QLabel(f"{human_size(d.used)} из {human_size(d.total)}" if d.ready else "нет носителя")
            info.setObjectName("muted")
            row.addWidget(name, 1)
            row.addWidget(info)
            v.addLayout(row)
            bar = UsageBar()
            bar.fraction = (d.used / d.total) if d.total else 0
            v.addWidget(bar)
            self.drives_box.addWidget(w)

    def _apply(self, st: dict) -> None:
        self.c_files.set_value(st["files"], lambda v: fmt_int(int(v)), f"скрытых: {fmt_int(st['hidden'])}")
        self.c_size.set_value(st["size"], lambda v: human_size(v), f"без расширения: {fmt_int(st['noext'])} файлов")
        self.c_dirs.set_value(st["dirs"], lambda v: fmt_int(int(v)))
        self.c_err.set_value(st["errors"], lambda v: fmt_int(int(v)),
                             "нет доступа, заняты, отключены" if st["errors"] else "всё доступно")
        cats = sorted(st["by_category"], key=lambda r: -r[2])
        items = [(c, ft.CAT_LABELS_PLURAL.get(c, c), float(sz), ft.CAT_COLORS.get(c, "#6B7385"), human_size(sz))
                 for c, n, sz in cats if sz > 0]
        self.donut.set_data(items, human_size(st["size"]), "в индексе")
        bars = []
        for ext, n, sz in st["top_ext"]:
            cat = ft.category_for(ext) if ext else "other"
            bars.append(("." + ext if ext else "(без расш.)", float(n), ft.CAT_COLORS.get(cat, "#6B7385"), fmt_int(n)))
        self.bars.set_data(bars)
        self.largest.clear()
        for path, size in st["largest"]:
            it = QListWidgetItem(f"{human_size(size):>9}   {os.path.basename(path)}")
            it.setToolTip(path)
            it.setData(Qt.UserRole, path)
            self.largest.addItem(it)
        sess = self.db.get_session()
        if sess and sess.get("finished"):
            when = datetime.fromtimestamp(sess["finished"]).strftime("%d.%m.%Y %H:%M")
            names = {"quick": "быстрое", "full": "полное", "deep": "глубокое"}
            state = {"done": "завершено", "interrupted": "прервано (можно продолжить)", "failed": "завершилось ошибкой"}
            self.last.setText(f"Последнее сканирование: {names.get(sess['mode'], sess['mode'])}, "
                              f"{state.get(sess['status'], sess['status'])} — {when}")
        else:
            self.last.setText("Сканирование ещё не запускалось.")
