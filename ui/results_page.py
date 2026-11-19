"""Страница результатов: таблица найденных файлов, фильтры, действия, экспорт."""
from __future__ import annotations

import os
import time
from typing import Optional

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QMenu, QMessageBox, QPushButton, QStackedLayout, QTableView, QVBoxLayout, QWidget)

import winutils as wu
from database import Database
from duplicate_detector import DuplicateDetector
from exporter import export_results
from filters import FileFilter, FilterError
from filetypes import CAT_LABELS_PLURAL
from search import SearchEngine, SearchResult, parse_query
from settings import Settings
from ui import actions
from ui.dialogs import PropertiesDialog
from ui.models import (COL_SORT, SORT_COL, FileTableModel, R_PATH, SearchRunner)
from ui.theme import palette
from ui.widgets import AnimButton, TaskThread, fade_in
from utils import fmt_int, human_size

SCOPE_TITLES = {"all": "Все файлы", "documents": "Документы", "images": "Изображения", "video": "Видео",
                "audio": "Аудио", "archives": "Архивы", "apps": "Приложения", "duplicates": "Дубликаты"}

TYPE_ITEMS = [("Все типы", None), ("Документы", "documents"), ("Изображения", "images"), ("Видео", "video"),
              ("Аудио", "audio"), ("Архивы", "archives"), ("Приложения", "apps"), ("Исходный код", "code"),
              ("Системные", "system"), ("Временные и резервные", "temp"), ("Без расширения", "noext"),
              ("Прочее", "other")]
SIZE_ITEMS = [("Любой размер", None), ("Больше 1 МБ", "size:>1mb"), ("Больше 10 МБ", "size:>10mb"),
              ("Больше 100 МБ", "size:>100mb"), ("Больше 1 ГБ", "size:>1gb"), ("Меньше 100 КБ", "size:<100kb"),
              ("Пустые (0 байт)", "size:0")]
DATE_ITEMS = [("Любая дата изменения", None), ("Сегодня", "modified:today"), ("За 7 дней", "modified:7d"),
              ("За 30 дней", "modified:30d"), ("За год", "modified:365d"), ("Старше года", "modified:<365d_old")]
CHIPS = [("Скрытые", "hidden:yes"), ("Системные", "system:yes"), ("Временные", "temp:yes"),
         ("Без расширения", "noext:yes"), ("Недоступные", "access:no"), ("Тип ≠ расширению", "mismatch:yes")]


class ResultsPage(QWidget):
    message = Signal(str)
    searchFinished = Signal(int, str)          # число найденных, текст запроса
    findDuplicatesRequested = Signal()
    queryRewritten = Signal(str)               # страница сама изменила текст запроса

    def __init__(self, db: Database, settings: Settings, parent=None):
        super().__init__(parent)
        self.db, self.settings = db, settings
        self.engine = SearchEngine(db)
        self.runner = SearchRunner(self.engine, self)
        self.runner.finished.connect(self._on_result)
        self.runner.failed.connect(self._on_failed)
        self.scope = "all"
        self.query = ""
        self.sort_key, self.desc = "name", False
        self._token = 0
        self._flt: Optional[FileFilter] = None
        self._pending_scroll: Optional[int] = None
        self._export_thread: Optional[TaskThread] = None
        self._copy_thread: Optional[TaskThread] = None
        self.scanning = False
        self.last_duration = 0.0
        self._t0 = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # заголовок + действия
        head = QHBoxLayout()
        head.setSpacing(8)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        self.title = QLabel("Все файлы")
        self.title.setObjectName("h1")
        self.count = QLabel("")
        self.count.setObjectName("muted")
        tl.addWidget(self.title)
        tl.addWidget(self.count)
        head.addLayout(tl, 1)
        self.btn_open = AnimButton("Открыть", "open")
        self.btn_loc = AnimButton("В папке", "location")
        self.btn_path = AnimButton("Путь", "copy")
        self.btn_props = AnimButton("Свойства", "info")
        self.btn_copy = AnimButton("Копировать файл", "layers")
        self.btn_csv = AnimButton("CSV", "export")
        self.btn_json = AnimButton("JSON", "export")
        for b, tip in ((self.btn_open, "Открыть файл (Enter)"), (self.btn_loc, "Показать в Проводнике"),
                       (self.btn_path, "Копировать полный путь (Ctrl+C)"), (self.btn_props, "Свойства файла (Alt+Enter)"),
                       (self.btn_copy, "Копировать файл в папку…"), (self.btn_csv, "Экспорт результатов в CSV"),
                       (self.btn_json, "Экспорт результатов в JSON")):
            b.setToolTip(tip)
            head.addWidget(b)
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_loc.clicked.connect(self.reveal_selected)
        self.btn_path.clicked.connect(self.copy_paths)
        self.btn_props.clicked.connect(self.show_properties)
        self.btn_copy.clicked.connect(self.copy_to_folder)
        self.btn_csv.clicked.connect(lambda: self.export("csv"))
        self.btn_json.clicked.connect(lambda: self.export("json"))
        root.addLayout(head)

        # фильтры
        self.filter_row = QHBoxLayout()
        self.filter_row.setSpacing(8)
        self.type_combo = QComboBox()
        for t, k in TYPE_ITEMS:
            self.type_combo.addItem(t, k)
        self.size_combo = QComboBox()
        for t, k in SIZE_ITEMS:
            self.size_combo.addItem(t, k)
        self.date_combo = QComboBox()
        for t, k in DATE_ITEMS:
            self.date_combo.addItem(t, k)
        for cb in (self.type_combo, self.size_combo, self.date_combo):
            cb.currentIndexChanged.connect(self._filters_changed)
            cb.setMinimumWidth(170)
            self.filter_row.addWidget(cb)
        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(8)
        self.chips: list[tuple[QPushButton, str]] = []
        for text, tok in CHIPS:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("chip", True)
            b.setCursor(Qt.PointingHandCursor)
            b.toggled.connect(self._filters_changed)
            self.chip_row.addWidget(b)
            self.chips.append((b, tok))
        self.chip_row.addStretch(1)
        self.btn_large = AnimButton("Крупные файлы", "chart", "ghost")
        self.btn_large.setToolTip("Показать самые большие файлы (порог задаётся в настройках)")
        self.btn_large.clicked.connect(self.show_large)
        self.filter_row.addWidget(self.btn_large)
        self.filter_row.addStretch(1)
        root.addLayout(self.filter_row)
        root.addLayout(self.chip_row)

        # баннер дубликатов
        self.dup_banner = QFrame()
        self.dup_banner.setObjectName("card")
        bl = QHBoxLayout(self.dup_banner)
        bl.setContentsMargins(16, 10, 12, 10)
        self.dup_label = QLabel("")
        self.dup_label.setWordWrap(True)
        bl.addWidget(self.dup_label, 1)
        self.dup_btn = AnimButton("Найти дубликаты", "duplicates", "primary")
        self.dup_btn.clicked.connect(self.findDuplicatesRequested.emit)
        bl.addWidget(self.dup_btn)
        root.addWidget(self.dup_banner)

        # таблица + пустое состояние
        self.model = FileTableModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.doubleClicked.connect(lambda _: self.open_selected())
        hh = self.table.horizontalHeader()
        hh.setSectionsMovable(True)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSortIndicatorShown(True)
        hh.setSectionsClickable(True)
        hh.setStretchLastSection(False)
        for i, w in enumerate((240, 64, 340, 84, 122, 122, 200, 110, 110, 150)):
            self.table.setColumnWidth(i, w)
        hh.setSectionResizeMode(2, QHeaderView.Interactive)
        hh.sectionClicked.connect(self._header_clicked)
        self.table.setColumnHidden(9, True)
        self.table.selectionModel().selectionChanged.connect(self._sel_changed)
        self.empty = QLabel("")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        self.stack_host = QWidget()
        self.stack = QStackedLayout(self.stack_host)
        self.stack.setStackingMode(QStackedLayout.StackAll)
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty)
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents)
        root.addWidget(self.stack_host, 1)

        self.err = QLabel("")
        self.err.setStyleSheet(f"color: {palette()['danger']};")
        self.err.hide()
        root.addWidget(self.err)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.refresh)
        self._sel_changed()
        self._update_sort_indicator()

    # ------------------------------------------------------------------ состояние
    def set_scope(self, scope: str) -> None:
        self.scope = scope
        self.title.setText(SCOPE_TITLES.get(scope, scope))
        is_dup = scope == "duplicates"
        self.dup_banner.setVisible(is_dup)
        self.type_combo.setVisible(scope == "all")
        for w in (self.size_combo, self.date_combo, self.btn_large):
            w.setVisible(True)
        self.model.dup_mode = is_dup
        self.table.setColumnHidden(9, not is_dup)
        self.table.horizontalHeader().setSortIndicatorShown(not is_dup)
        self.refresh_soon(0)

    def set_query(self, text: str) -> None:
        self.query = text
        self.refresh_soon()

    def set_scanning(self, on: bool) -> None:
        self.scanning = on
        self.dup_btn.setEnabled(not on)

    def refresh_soon(self, ms: int = 250) -> None:
        self._debounce.start(ms)

    def _filters_changed(self, *_):
        self.refresh_soon(60)

    def _panel_filter_text(self) -> str:
        parts = []
        for combo in (self.size_combo, self.date_combo):
            v = combo.currentData()
            if v == "modified:<365d_old":
                from datetime import datetime, timedelta
                v = "modified:<" + (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
            if v:
                parts.append(v)
        for b, tok in self.chips:
            if b.isChecked():
                parts.append(tok)
        return " ".join(parts)

    def build_filter(self) -> FileFilter:
        flt = parse_query(self.query)
        flt = flt.combined(parse_query(self._panel_filter_text()))
        if self.scope == "duplicates":
            flt.duplicates = True
        elif self.scope != "all":
            flt = flt.combined(FileFilter(types=[self.scope]))
        elif self.type_combo.currentData():
            flt = flt.combined(FileFilter(types=[self.type_combo.currentData()]))
        return flt

    def refresh(self, keep_scroll: bool = False) -> None:
        try:
            flt = self.build_filter()
        except FilterError as exc:
            self.err.setText(f"⚠ {exc}")
            self.err.show()
            return
        self.err.hide()
        self._flt = flt
        self._pending_scroll = self.table.verticalScrollBar().value() if keep_scroll else None
        sort = "name" if self.scope == "duplicates" else self.sort_key
        self._t0 = time.time()
        self._token = self.runner.run(flt, sort, self.desc)

    def _on_failed(self, token: int, msg: str) -> None:
        if "interrupt" in msg.lower():
            return
        self.err.setText(f"⚠ Ошибка поиска: {msg}")
        self.err.show()

    def _on_result(self, token: int, res: SearchResult) -> None:
        if token != self._token:
            res.close()
            return
        self.last_duration = time.time() - self._t0
        self.model.set_result(res, self.scope == "duplicates")
        if self._pending_scroll is not None:
            self.table.verticalScrollBar().setValue(self._pending_scroll)
        else:
            self.table.scrollToTop()
        self.count.setText(f"Найдено: {fmt_int(res.count)} · {human_size(res.total_size)}")
        totals_empty = res.count == 0
        if totals_empty:
            if self.scanning:
                self.empty.setText("Идёт сканирование — результаты появляются по мере обнаружения файлов…")
            elif not self.db.has_files():
                self.empty.setText("Индекс пуст.\nНажмите «Сканировать», чтобы найти файлы на выбранных дисках.")
            elif self.scope == "duplicates":
                self.empty.setText("Дубликатов не найдено.\nНажмите «Найти дубликаты»: будут посчитаны SHA-256 "
                                   "у файлов с одинаковым размером.")
            else:
                self.empty.setText("Ничего не найдено.\nПопробуйте изменить запрос или фильтры.")
        self.empty.setVisible(totals_empty)
        if self.scope == "duplicates":
            self._update_dup_summary()
        self.searchFinished.emit(res.count, self.query)
        self._sel_changed()

    def _update_dup_summary(self) -> None:
        if getattr(self, "_dup_thread", None) and self._dup_thread.isRunning():
            return

        def work(progress, stop):
            det = DuplicateDetector(self.db, self.settings)
            return det.summary(), det.pending_hash_count()
        t = TaskThread(work, self)
        t.done.connect(self._dup_summary_ready)
        self._dup_thread = t
        t.start()

    def _dup_summary_ready(self, res) -> None:
        s, pending = res
        txt = (f"Групп дубликатов: <b>{fmt_int(s['groups'])}</b> · файлов в группах: <b>{fmt_int(s['files'])}</b> · "
               f"лишние копии занимают: <b>{human_size(s['wasted'])}</b>")
        if pending:
            txt += (f"<br><span style='color:{palette()['warn']}'>Ещё не сравнено по SHA-256: {fmt_int(pending)} файлов "
                    f"с совпадающим размером — нажмите «Найти дубликаты».</span>")
        self.dup_label.setText(txt)

    def show_large(self) -> None:
        self.size_combo.blockSignals(True)
        self.size_combo.setCurrentIndex(0)
        self.size_combo.blockSignals(False)
        mb = max(1, self.settings.large_file_mb)
        self.query = (self.query + f" size:>={mb}mb").strip()
        self.sort_key, self.desc = "size", True
        self._update_sort_indicator()
        self.message.emit(f"К запросу добавлено условие size:>={mb}mb, сортировка по размеру")
        self.queryRewritten.emit(self.query)
        self.refresh()

    # ------------------------------------------------------------------ сортировка
    def _header_clicked(self, col: int) -> None:
        if col not in COL_SORT or self.scope == "duplicates":
            return
        key = COL_SORT[col]
        if key == self.sort_key:
            self.desc = not self.desc
        else:
            self.sort_key, self.desc = key, key in ("size", "ctime", "mtime")
        self._update_sort_indicator()
        self.refresh()

    def _update_sort_indicator(self) -> None:
        col = SORT_COL.get(self.sort_key, 0)
        self.table.horizontalHeader().setSortIndicator(col, Qt.DescendingOrder if self.desc else Qt.AscendingOrder)

    # ------------------------------------------------------------------ выделение и действия
    def selected_rows(self, limit: int = 10000) -> list[tuple]:
        rows = []
        for idx in self.table.selectionModel().selectedRows():
            r = self.model.row_at(idx.row())
            if r:
                rows.append(r)
            if len(rows) >= limit:
                break
        return rows

    def _sel_changed(self, *_):
        n = len(self.table.selectionModel().selectedRows()) if self.table.selectionModel() else 0
        for b in (self.btn_open, self.btn_loc, self.btn_props):
            b.setEnabled(n == 1)
        for b in (self.btn_path, self.btn_copy):
            b.setEnabled(n >= 1)
        has = bool(self.model.result and self.model.result.count)
        self.btn_csv.setEnabled(has)
        self.btn_json.setEnabled(has)

    def _one(self) -> Optional[tuple]:
        rows = self.selected_rows(1)
        return rows[0] if rows else None

    def _do(self, fn, path: str, what: str) -> None:
        try:
            fn(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, what, f"Не удалось выполнить действие:\n{path}\n\n{exc}")

    def open_selected(self) -> None:
        r = self._one()
        if not r:
            return
        if not os.path.exists(wu.to_long_path(r[R_PATH])):
            QMessageBox.information(self, "Открыть", "Файл не найден: он удалён, перемещён или диск отключён.\n"
                                    "Обновите индекс, чтобы убрать устаревшие записи.")
            return
        self._do(wu.open_path, r[R_PATH], "Открыть файл")

    def reveal_selected(self) -> None:
        r = self._one()
        if r:
            self._do(wu.reveal_in_explorer, r[R_PATH], "Открыть расположение")

    def copy_paths(self) -> None:
        rows = self.selected_rows()
        if rows:
            actions.copy_paths_to_clipboard([r[R_PATH] for r in rows])
            self.message.emit(f"Скопировано путей: {len(rows)}")

    def show_properties(self) -> None:
        r = self._one()
        if r:
            PropertiesDialog(r, self).exec()

    def copy_to_folder(self) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        dest = QFileDialog.getExistingDirectory(self, "Куда скопировать выбранные файлы?")
        if not dest:
            return
        paths = [r[R_PATH] for r in rows]
        self.btn_copy.setEnabled(False)
        t = TaskThread(lambda prog, stop: actions.copy_files_to_folder(paths, dest, prog, stop), self)
        t.progress.connect(lambda name, a, b: self.message.emit(f"Копирование {a + 1}/{b}: {name}"))
        t.done.connect(self._copy_done)
        t.failed.connect(lambda m: QMessageBox.warning(self, "Копирование", m))
        t.finished.connect(self._sel_changed)
        self._copy_thread = t
        t.start()

    def _copy_done(self, result) -> None:
        done, errors = result
        self.message.emit(f"Скопировано файлов: {done}" + (f", ошибок: {len(errors)}" if errors else ""))
        if errors:
            QMessageBox.warning(self, "Копирование", "Не все файлы удалось скопировать:\n\n" + "\n".join(errors[:15]))

    def copy_files_clipboard(self) -> None:
        rows = self.selected_rows(500)
        if rows:
            actions.copy_files_to_clipboard([r[R_PATH] for r in rows])
            self.message.emit("Файлы скопированы в буфер обмена — вставьте их в нужную папку (Ctrl+V)")

    def _menu(self, pos) -> None:
        idx = self.table.indexAt(pos)
        if idx.isValid() and not self.table.selectionModel().isRowSelected(idx.row()):
            self.table.selectRow(idx.row())
        n = len(self.table.selectionModel().selectedRows())
        if not n:
            return
        m = QMenu(self)
        a = m.addAction("Открыть")
        a.setEnabled(n == 1)
        a.triggered.connect(self.open_selected)
        a = m.addAction("Открыть расположение в Проводнике")
        a.setEnabled(n == 1)
        a.triggered.connect(self.reveal_selected)
        m.addSeparator()
        m.addAction("Копировать полный путь").triggered.connect(self.copy_paths)
        m.addAction("Копировать файл в буфер обмена").triggered.connect(self.copy_files_clipboard)
        m.addAction("Копировать в папку…").triggered.connect(self.copy_to_folder)
        m.addSeparator()
        a = m.addAction("Свойства")
        a.setEnabled(n == 1)
        a.triggered.connect(self.show_properties)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.table.hasFocus():
            if e.modifiers() & Qt.AltModifier:
                self.show_properties()
            else:
                self.open_selected()
            return
        if e.matches(e.StandardKey.Copy) and self.table.hasFocus():
            self.copy_paths()
            return
        super().keyPressEvent(e)

    # ------------------------------------------------------------------ экспорт
    def export(self, fmt: str) -> None:
        if not self._flt or not self.model.result or not self.model.result.count:
            return
        if self._export_thread and self._export_thread.isRunning():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт результатов", f"deepsearch-results.{fmt}",
                                              "CSV (*.csv)" if fmt == "csv" else "JSON (*.json)")
        if not path:
            return
        flt, sort, desc = self._flt, ("name" if self.scope == "duplicates" else self.sort_key), self.desc
        total = self.model.result.count
        t = TaskThread(lambda prog, stop: export_results(
            self.engine, flt, sort, desc, path, fmt,
            progress=lambda n: prog(f"Экспорт: {fmt_int(n)} из {fmt_int(total)}", n, total),
            should_stop=stop.is_set), self)
        t.progress.connect(lambda txt, a, b: self.message.emit(txt))
        t.done.connect(lambda n: self.message.emit(f"Экспортировано {fmt_int(n)} записей → {path}"))
        t.failed.connect(lambda m: QMessageBox.warning(self, "Экспорт", f"Не удалось выполнить экспорт:\n{m}"))
        self._export_thread = t
        self.message.emit("Экспорт…")
        t.start()

    def shutdown(self) -> None:
        for t in (self._export_thread, self._copy_thread, getattr(self, "_dup_thread", None)):
            if t and t.isRunning():
                t.stop_event.set()
                t.wait(3000)
        self.model.set_result(None)
