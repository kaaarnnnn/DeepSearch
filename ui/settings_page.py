"""Страница настроек."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMessageBox, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

import winutils as wu
from database import Database
from settings import DEFAULT_TEXT_EXTS, Settings
from ui.widgets import AnimButton, Card
from utils import app_data_dir, human_size


class SettingsPage(QWidget):
    themeChanged = Signal(str)
    animationsChanged = Signal(bool)
    indexCleared = Signal()
    restartAdmin = Signal()

    def __init__(self, db: Database, settings: Settings, parent=None):
        super().__init__(parent)
        self.db, self.s = db, settings
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
        h = QLabel("Настройки")
        h.setObjectName("h1")
        lay.addWidget(h)

        # --- внешний вид
        card, form = self._card("Внешний вид", lay)
        self.theme = QComboBox()
        self.theme.addItem("Тёмная", "dark")
        self.theme.addItem("Светлая", "light")
        self.theme.setCurrentIndex(0 if settings.theme == "dark" else 1)
        self.theme.currentIndexChanged.connect(self._theme)
        form.addRow("Тема", self.theme)
        self.anim = QCheckBox("Плавные анимации интерфейса")
        self.anim.setChecked(settings.animations)
        self.anim.toggled.connect(lambda v: self._set("animations", v, self.animationsChanged))
        form.addRow("", self.anim)

        # --- сканирование
        card, form = self._card("Сканирование", lay)
        self.threads = QSpinBox()
        self.threads.setRange(0, 64)
        self.threads.setSpecialValueText("Авто")
        self.threads.setValue(settings.threads)
        self.threads.setToolTip("Потоков обхода каталогов. «Авто» — 4. Для сетевых дисков и HDD можно экспериментировать.")
        self.threads.valueChanged.connect(lambda v: self._set("threads", v))
        form.addRow("Потоков обхода", self.threads)
        self.quick_list = QListWidget()
        self.quick_list.setMaximumHeight(110)
        self._fill_quick()
        row = QHBoxLayout()
        for text, fn in (("Добавить папку", self._add_quick), ("Убрать", self._del_quick), ("Сбросить", self._reset_quick)):
            b = AnimButton(text, variant="ghost")
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        qw = QWidget()
        ql = QVBoxLayout(qw)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.addWidget(self.quick_list)
        ql.addLayout(row)
        form.addRow("Каталоги быстрого сканирования", qw)
        self.skip_hidden = QCheckBox("В быстром режиме пропускать скрытые и системные папки")
        self.skip_hidden.setChecked(settings.quick_skip_hidden_dirs)
        self.skip_hidden.toggled.connect(lambda v: self._set("quick_skip_hidden_dirs", v))
        form.addRow("", self.skip_hidden)
        self.excl_names = QLineEdit(", ".join(settings.exclude_names))
        self.excl_names.setPlaceholderText("например: node_modules, .git  (пусто = ничего не пропускать)")
        self.excl_names.editingFinished.connect(
            lambda: self._set("exclude_names", [x.strip() for x in self.excl_names.text().split(",") if x.strip()]))
        form.addRow("Всегда пропускать папки по имени", self.excl_names)
        self.excl_paths = QLineEdit("; ".join(settings.exclude_paths))
        self.excl_paths.setPlaceholderText(r"например: D:\Backups; C:\Temp")
        self.excl_paths.editingFinished.connect(
            lambda: self._set("exclude_paths", [x.strip() for x in self.excl_paths.text().split(";") if x.strip()]))
        form.addRow("Всегда пропускать пути", self.excl_paths)
        self.auto_upd = QCheckBox("Обновлять индекс при запуске программы (инкрементально)")
        self.auto_upd.setChecked(settings.auto_update_on_start)
        self.auto_upd.toggled.connect(lambda v: self._set("auto_update_on_start", v))
        form.addRow("", self.auto_upd)
        note = QLabel("Символические ссылки и точки соединения (junction) не разворачиваются — это защита от "
                      "бесконечных циклов. Их количество показывается после сканирования.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow("", note)

        # --- глубокое сканирование
        card, form = self._card("Глубокое сканирование и дубликаты", lay)
        self.content = QCheckBox("Индексировать текст внутри файлов (txt, код, docx, xlsx, pptx, odt, pdf, doc/xls/ppt*)")
        self.content.setChecked(settings.content_index)
        self.content.toggled.connect(lambda v: self._set("content_index", v))
        form.addRow("", self.content)
        self.cmax = QSpinBox()
        self.cmax.setRange(16, 4096)
        self.cmax.setSuffix(" КБ")
        self.cmax.setValue(settings.content_max_kb)
        self.cmax.valueChanged.connect(lambda v: self._set("content_max_kb", v))
        form.addRow("Текста на файл в индексе", self.cmax)
        self.cfile = QSpinBox()
        self.cfile.setRange(1, 4096)
        self.cfile.setSuffix(" МБ")
        self.cfile.setValue(settings.content_max_file_mb)
        self.cfile.valueChanged.connect(lambda v: self._set("content_max_file_mb", v))
        form.addRow("Макс. размер docx/pdf для разбора", self.cfile)
        self.text_exts = QLineEdit(" ".join(settings.text_exts))
        self.text_exts.editingFinished.connect(
            lambda: self._set("text_exts", [x.strip().lstrip(".").lower() for x in self.text_exts.text().replace(",", " ").split() if x.strip()] or list(DEFAULT_TEXT_EXTS)))
        form.addRow("Расширения текстовых файлов", self.text_exts)
        self.hash_all = QCheckBox("Считать SHA-256 для всех файлов (иначе — только для файлов с одинаковым размером)")
        self.hash_all.setChecked(settings.hash_all_files)
        self.hash_all.toggled.connect(lambda v: self._set("hash_all_files", v))
        form.addRow("", self.hash_all)
        self.min_dup = QSpinBox()
        self.min_dup.setRange(1, 10 ** 9)
        self.min_dup.setSuffix(" Б")
        self.min_dup.setValue(settings.min_dup_size)
        self.min_dup.valueChanged.connect(lambda v: self._set("min_dup_size", v))
        form.addRow("Мин. размер для поиска дубликатов", self.min_dup)
        note = QLabel("* Для doc/xls/ppt используется упрощённое извлечение строк, а не полный разбор формата. "
                      "Для PDF нужен пакет pypdf; PDF без текстового слоя (сканы) и зашифрованные не индексируются.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow("", note)

        # --- поиск
        card, form = self._card("Поиск и история", lay)
        self.large = QSpinBox()
        self.large.setRange(1, 10 ** 6)
        self.large.setSuffix(" МБ")
        self.large.setValue(settings.large_file_mb)
        self.large.valueChanged.connect(lambda v: self._set("large_file_mb", v))
        form.addRow("Порог «крупных файлов»", self.large)
        clr = AnimButton("Очистить историю запросов", "history")
        clr.clicked.connect(self._clear_history)
        form.addRow("", clr)

        # --- данные и доступ
        card, form = self._card("Данные и доступ", lay)
        self.db_label = QLabel()
        self.db_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.db_label.setWordWrap(True)
        form.addRow("Индекс", self.db_label)
        row = QHBoxLayout()
        b1 = AnimButton("Открыть папку данных", "folder")
        b1.clicked.connect(lambda: wu.open_path(str(app_data_dir())))
        b2 = AnimButton("Очистить индекс", "stop", "danger")
        b2.setToolTip("Удаляет только базу индекса DeepSearch. Ваши файлы не затрагиваются.")
        b2.clicked.connect(self._clear_index)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        rw = QWidget()
        rw.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        form.addRow("", rw)
        adm = wu.is_admin()
        self.admin_label = QLabel("Права администратора: " + ("да" if adm else "нет"))
        form.addRow("", self.admin_label)
        if wu.IS_WIN and not adm:
            b3 = AnimButton("Перезапустить от администратора", "shield")
            b3.clicked.connect(self.restartAdmin.emit)
            form.addRow("", b3)
        lay.addStretch(1)
        self.update_info()

    # ------------------------------------------------------------------
    def _card(self, title: str, lay: QVBoxLayout):
        card = Card()
        v = QVBoxLayout(card)
        v.setContentsMargins(20, 16, 20, 18)
        v.setSpacing(10)
        t = QLabel(title)
        t.setObjectName("h2")
        v.addWidget(t)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignTop)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        v.addLayout(form)
        lay.addWidget(card)
        return card, form

    def _set(self, attr: str, value, signal=None) -> None:
        setattr(self.s, attr, value)
        self.s.save()
        if signal is not None:
            signal.emit(value)

    def _theme(self) -> None:
        self._set("theme", self.theme.currentData(), self.themeChanged)

    def _fill_quick(self) -> None:
        self.quick_list.clear()
        for f in (self.s.quick_folders or wu.default_quick_folders()):
            self.quick_list.addItem(f)

    def _add_quick(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "Папка для быстрого сканирования")
        if p:
            cur = list(self.s.quick_folders or wu.default_quick_folders())
            p = wu.normalize_root(p)
            if p not in cur:
                cur.append(p)
            self._set("quick_folders", cur)
            self._fill_quick()

    def _del_quick(self) -> None:
        it = self.quick_list.currentItem()
        if it:
            cur = [f for f in (self.s.quick_folders or wu.default_quick_folders()) if f != it.text()]
            self._set("quick_folders", cur)
            self._fill_quick()

    def _reset_quick(self) -> None:
        self._set("quick_folders", [])
        self._fill_quick()

    def _clear_history(self) -> None:
        self.db.clear_history()
        QMessageBox.information(self, "История", "История поисковых запросов очищена.")

    def _clear_index(self) -> None:
        r = QMessageBox.question(self, "Очистить индекс",
                                 "Будет удалена только база индекса DeepSearch (список найденных файлов, дубликаты, "
                                 "индекс текста). Сами файлы на дисках не затрагиваются.\n\nПродолжить?")
        if r == QMessageBox.Yes:
            try:
                self.db.clear_index()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Очистка индекса", f"Не удалось очистить индекс:\n{exc}")
                return
            self.update_info()
            self.indexCleared.emit()

    def update_info(self) -> None:
        try:
            size = os.path.getsize(self.db.path)
        except OSError:
            size = 0
        self.db_label.setText(f"{self.db.path}\nРазмер: {human_size(size)}")
