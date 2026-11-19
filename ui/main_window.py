"""Главное окно: боковая панель, строка поиска, управление сканированием, страницы, нижняя панель."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from PySide6.QtCore import QRectF, QEasingCurve, QStringListModel, Qt, QThread, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QApplication, QCompleter, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
                               QMessageBox, QComboBox, QStackedWidget, QVBoxLayout, QWidget, QSizePolicy)

import winutils as wu
from database import Database
from duplicate_detector import DuplicateDetector
from filters import FilterError
from scanner import ScanEngine, resolve_roots
from search import SYNTAX_HELP
from settings import Settings
from ui import icons, theme, widgets
from ui.dialogs import DrivePopup, ErrorsDialog
from ui.home_page import HomePage
from ui.icons import draw_glyph
from ui.results_page import ResultsPage
from ui.settings_page import SettingsPage
from ui.widgets import (AnimButton, ElidedLabel, SidebarButton, SmoothProgress, TaskThread, fade_in)
from utils import APP_VERSION, fmt_duration, fmt_int, human_size

log = logging.getLogger("deepsearch.ui")

NAV = [("home", "Главная", "home"), ("all", "Все файлы", "files"), ("documents", "Документы", "documents"),
       ("images", "Изображения", "images"), ("video", "Видео", "video"), ("audio", "Аудио", "audio"),
       ("archives", "Архивы", "archives"), ("apps", "Приложения", "apps"), ("duplicates", "Дубликаты", "duplicates"),
       ("settings", "Настройки", "settings")]
MODE_NAMES = {"quick": "Быстрое сканирование", "full": "Полное сканирование", "deep": "Глубокое сканирование"}


class SearchBox(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("search")
        self.setClearButtonEnabled(True)
        self.setPlaceholderText("Поиск: имя, *.pdf, ext:docx, size:>10mb, modified:2024, content:текст …")
        self.setMinimumHeight(40)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        draw_glyph(p, "search", QRectF(11, (self.height() - 18) / 2, 18, 18), QColor(theme.palette()["muted"]))


class DrivePoller(QThread):
    """Опрашивает список дисков в фоне (подключение/отключение внешних накопителей)."""
    drivesChanged = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._last = None

    def run(self):
        while not self._stop.is_set():
            try:
                drives = wu.list_drives()
                sig = [(d.root, d.label, d.ready, d.total // (1 << 26), d.free // (1 << 26)) for d in drives]
                if sig != self._last:
                    self._last = sig
                    self.drivesChanged.emit(drives)
            except Exception:  # noqa: BLE001
                log.exception("Опрос дисков")
            self._stop.wait(3.0)

    def stop(self):
        self._stop.set()
        self.wait(5000)


class MainWindow(QMainWindow):
    def __init__(self, db: Database, settings: Settings):
        super().__init__()
        self.db, self.s = db, settings
        self.engine: Optional[ScanEngine] = None
        self.dup_task: Optional[TaskThread] = None
        self.current = "home"
        self._stopping = False
        self._last_refresh = 0.0
        self._scan_started = 0.0
        self._idle_thread = None
        self._known_drives: dict[str, wu.DriveInfo] = {}
        self._first_drives = True
        self.setWindowTitle("DeepSearch")
        self.setWindowIcon(icons.app_icon())
        self.resize(1360, 840)
        self.setMinimumSize(900, 600)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        outer.addLayout(h, 1)

        # ---------------- боковая панель
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self._side_w = 214
        self.sidebar.setFixedWidth(self._side_w)
        sv = QVBoxLayout(self.sidebar)
        sv.setContentsMargins(0, 14, 0, 12)
        sv.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(18, 0, 10, 10)
        self.logo = QLabel()
        self.logo.setPixmap(icons.app_icon_pixmap(64).scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.brand_name = QLabel("DeepSearch")
        self.brand_name.setObjectName("h2")
        brand.addWidget(self.logo)
        brand.addWidget(self.brand_name, 1)
        sv.addLayout(brand)
        self.nav: dict[str, SidebarButton] = {}
        for key, text, icon in NAV:
            if key == "settings":
                sv.addStretch(1)
            b = SidebarButton(key, text, icon)
            b.clicked.connect(lambda _=False, k=key: self.navigate(k))
            sv.addWidget(b)
            self.nav[key] = b
        self.ver = QLabel(f"v{APP_VERSION}")
        self.ver.setObjectName("muted")
        self.ver.setContentsMargins(20, 6, 0, 0)
        sv.addWidget(self.ver)
        h.addWidget(self.sidebar)

        # ---------------- центральная область
        content = QWidget()
        content.setObjectName("content")
        cv = QVBoxLayout(content)
        cv.setContentsMargins(22, 18, 22, 0)
        cv.setSpacing(12)
        h.addWidget(content, 1)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.search = SearchBox()
        self.search.setToolTip(SYNTAX_HELP)
        self.search.textChanged.connect(self._text_changed)
        self.search.returnPressed.connect(self._save_history)
        self.completer = QCompleter(QStringListModel([]), self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.search.setCompleter(self.completer)
        top.addWidget(self.search, 1)
        self.btn_hist = AnimButton("", "history", "ghost")
        self.btn_hist.setToolTip("История поиска")
        self.btn_hist.clicked.connect(self._history_menu)
        self.btn_help = AnimButton("", "info", "ghost")
        self.btn_help.setToolTip("Справка по синтаксису поиска")
        self.btn_help.clicked.connect(lambda: QMessageBox.information(self, "Синтаксис поиска", SYNTAX_HELP))
        top.addWidget(self.btn_hist)
        top.addWidget(self.btn_help)
        self.mode = QComboBox()
        for k, v in MODE_NAMES.items():
            self.mode.addItem(v, k)
        self.mode.setCurrentIndex(max(0, self.mode.findData(settings.scan_mode)))
        self.mode.setMinimumHeight(38)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        top.addWidget(self.mode)
        self.btn_drives = AnimButton("Диски", "drive")
        self.btn_drives.clicked.connect(self._show_drives)
        top.addWidget(self.btn_drives)
        self.btn_scan = AnimButton("Сканировать", "scan", "primary")
        self.btn_scan.setMinimumWidth(150)
        self.btn_scan.clicked.connect(self._scan_clicked)
        top.addWidget(self.btn_scan)
        self.btn_resume = AnimButton("Продолжить", "play")
        self.btn_resume.setToolTip("Продолжить прерванное сканирование с места остановки")
        self.btn_resume.clicked.connect(lambda: self.start_scan(resume=True))
        top.addWidget(self.btn_resume)
        self.btn_update = AnimButton("", "refresh")
        self.btn_update.setToolTip("Обновить индекс: повторный проход по последним просканированным местам "
                                   "(изменённые файлы обновляются, удалённые убираются)")
        self.btn_update.clicked.connect(self.update_index)
        top.addWidget(self.btn_update)
        cv.addLayout(top)

        self.stack = QStackedWidget()
        self.home = HomePage(db, settings)
        self.results = ResultsPage(db, settings)
        self.settings_page = SettingsPage(db, settings)
        for w in (self.home, self.results, self.settings_page):
            self.stack.addWidget(w)
        cv.addWidget(self.stack, 1)

        # ---------------- нижняя панель
        self.statusbar = QFrame()
        self.statusbar.setObjectName("statusbar")
        sb = QVBoxLayout(self.statusbar)
        sb.setContentsMargins(22, 8, 22, 8)
        sb.setSpacing(6)
        self.progress = SmoothProgress()
        sb.addWidget(self.progress)
        row = QHBoxLayout()
        row.setSpacing(18)
        self.lbl_phase = QLabel("Готово")
        self.lbl_phase.setMinimumWidth(190)
        self.lbl_dir = ElidedLabel("")
        self.lbl_dir.setObjectName("muted")
        self.lbl_dirs = QLabel("Папок: 0")
        self.lbl_files = QLabel("Найдено: 0")
        self.lbl_speed = QLabel("Скорость: —")
        self.lbl_errors = QLabel("")
        self.lbl_errors.setCursor(Qt.PointingHandCursor)
        self.lbl_errors.mousePressEvent = lambda e: self.show_errors()
        for l in (self.lbl_dirs, self.lbl_files, self.lbl_speed, self.lbl_errors):
            l.setMinimumWidth(110)
        row.addWidget(self.lbl_phase)
        row.addWidget(self.lbl_dir, 1)
        row.addWidget(self.lbl_dirs)
        row.addWidget(self.lbl_files)
        row.addWidget(self.lbl_speed)
        row.addWidget(self.lbl_errors)
        sb.addLayout(row)
        outer.addWidget(self.statusbar)
        cv.setContentsMargins(22, 18, 22, 10)

        # ---------------- связи
        self.drive_popup = DrivePopup(settings, self)
        self.drive_popup.changed.connect(self._drives_selection_changed)
        self.home.scanRequested.connect(lambda m: self.start_scan(m))
        self.home.openErrors.connect(self.show_errors)
        self.home.openScope.connect(self._open_scope)
        self.home.revealRequested.connect(lambda p: wu.reveal_in_explorer(p))
        self.home.restartAdmin.connect(self.restart_admin)
        self.settings_page.restartAdmin.connect(self.restart_admin)
        self.settings_page.themeChanged.connect(self.apply_theme)
        self.settings_page.animationsChanged.connect(widgets.set_animations)
        self.settings_page.indexCleared.connect(self._index_cleared)
        self.results.message.connect(self.flash)
        self.results.searchFinished.connect(self._search_finished)
        self.results.findDuplicatesRequested.connect(self.find_duplicates)
        self.results.queryRewritten.connect(lambda t: (self.search.blockSignals(True), self.search.setText(t),
                                                       self.search.blockSignals(False)))

        self.poll = QTimer(self)
        self.poll.setInterval(250)
        self.poll.timeout.connect(self._poll)
        self.hist_timer = QTimer(self)
        self.hist_timer.setSingleShot(True)
        self.hist_timer.setInterval(1800)
        self.hist_timer.timeout.connect(self._save_history)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(lambda: self._idle_status())

        for seq, fn in (("Ctrl+F", lambda: (self.search.setFocus(), self.search.selectAll())),
                        ("F5", self._refresh_view), ("Esc", lambda: self.search.clear() if self.search.hasFocus() else None)):
            QShortcut(QKeySequence(seq), self, activated=fn)

        self.poller = DrivePoller(self)
        self.poller.drivesChanged.connect(self._drives_changed)
        self.poller.start()
        self.nav["home"].setChecked(True)
        self.stack.setCurrentWidget(self.home)
        self._update_buttons()
        self._idle_status()
        QTimer.singleShot(50, self.home.refresh)
        if settings.auto_update_on_start:
            QTimer.singleShot(800, self.update_index)

    # =================================================================== навигация
    def navigate(self, key: str) -> None:
        for k, b in self.nav.items():
            b.setChecked(k == key)
        self.current = key
        if key == "home":
            target = self.home
            self.home.refresh()
        elif key == "settings":
            target = self.settings_page
            self.settings_page.update_info()
        else:
            target = self.results
            self.results.set_scope(key)
            self.results.set_query(self.search.text())
        self.stack.setCurrentWidget(target)
        fade_in(target)

    def _open_scope(self, scope: str, query: str) -> None:
        self.search.blockSignals(True)
        self.search.setText(query)
        self.search.blockSignals(False)
        self.navigate(scope)

    def _text_changed(self, text: str) -> None:
        if self.current in ("home", "settings") and text.strip():
            self.navigate("all")
            return
        if self.current not in ("home", "settings"):
            self.results.set_query(text)
        self.hist_timer.start()

    def _refresh_view(self) -> None:
        if self.current == "home":
            self.home.refresh()
        elif self.current != "settings":
            self.results.refresh(keep_scroll=True)

    # =================================================================== история
    def _save_history(self) -> None:
        q = self.search.text().strip()
        if q and self.current not in ("home", "settings") and self.results.model.result is not None:
            self.db.add_history(q, self.results.model.result.count, self.s.history_limit)
            self._update_completer()

    def _search_finished(self, count: int, query: str) -> None:
        pass

    def _update_completer(self) -> None:
        self.completer.setModel(QStringListModel([q for q, _, _ in self.db.history(100)]))

    def _history_menu(self) -> None:
        self._update_completer()
        m = QMenu(self)
        hist = self.db.history(30)
        if not hist:
            a = m.addAction("История пуста")
            a.setEnabled(False)
        for q, ts, hits in hist:
            a = m.addAction(f"{q}    ·  {fmt_int(hits)}")
            a.triggered.connect(lambda _=False, text=q: (self.search.setText(text), self.search.setFocus()))
        if hist:
            m.addSeparator()
            m.addAction("Очистить историю").triggered.connect(lambda: (self.db.clear_history(), self._update_completer()))
        m.exec(self.btn_hist.mapToGlobal(self.btn_hist.rect().bottomLeft()))

    # =================================================================== диски
    def _drives_changed(self, drives: list) -> None:
        new = {d.root: d for d in drives}
        if not self._first_drives:
            for root, d in new.items():
                if root not in self._known_drives:
                    self.flash(f"Подключён диск {root.rstrip(chr(92))} ({d.title}) — отметьте его в списке «Диски»")
            for root in self._known_drives:
                if root not in new:
                    self.flash(f"Диск {root.rstrip(chr(92))} отключён — его файлы помечены как недоступные")
        elif not self.s.scan_roots and drives:
            sysdrive = (os.environ.get("SystemDrive", "C:") + "\\").upper()
            pick = [d.root for d in drives if d.root.upper() == sysdrive] or [drives[0].root]
            self.s.scan_roots = pick
            self.s.save()
        self._first_drives = False
        self._known_drives = new
        self.drive_popup.set_drives(drives)
        self.home.set_drives(drives)
        self.results.model.present_drives = {d.root.upper() for d in drives if d.ready} if wu.IS_WIN else None
        self.results.table.viewport().update()
        self._update_buttons()

    def _show_drives(self) -> None:
        self.drive_popup.set_drives(list(self._known_drives.values()))
        self.drive_popup.adjustSize()
        self.drive_popup.move(self.btn_drives.mapToGlobal(self.btn_drives.rect().bottomLeft()))
        self.drive_popup.show()

    def _drives_selection_changed(self) -> None:
        self._update_buttons()

    def _mode_changed(self) -> None:
        self.s.scan_mode = self.mode.currentData()
        self.s.save()
        self._update_buttons()

    # =================================================================== сканирование
    @property
    def scanning(self) -> bool:
        return self.engine is not None and not self.engine.finished

    @property
    def busy(self) -> bool:
        return self.scanning or (self.dup_task is not None and self.dup_task.isRunning())

    def _scan_clicked(self) -> None:
        if self.scanning:
            self._stopping = True
            self.engine.stop()
            self.btn_scan.setText("Остановка…")
            self.btn_scan.setEnabled(False)
        elif self.dup_task and self.dup_task.isRunning():
            self.dup_task.stop_event.set()
            self.btn_scan.setEnabled(False)
        else:
            self.start_scan(self.mode.currentData())

    def start_scan(self, mode: Optional[str] = None, resume: bool = False, roots: Optional[list] = None) -> None:
        if self.busy:
            QMessageBox.information(self, "DeepSearch", "Сначала дождитесь завершения текущей операции или остановите её.")
            return
        mode = mode or self.mode.currentData()
        if not resume:
            roots = roots if roots is not None else resolve_roots(mode, self.s, self.drive_popup.selected() or self.s.scan_roots)
            if not roots:
                if mode == "quick":
                    QMessageBox.information(self, "DeepSearch", "Не найдено ни одного каталога для быстрого "
                                            "сканирования. Добавьте их в настройках.")
                else:
                    self.flash("Выберите диски или папки для сканирования")
                    self._show_drives()
                return
            if mode != self.mode.currentData():
                self.mode.blockSignals(True)
                self.mode.setCurrentIndex(self.mode.findData(mode))
                self.mode.blockSignals(False)
                self.s.scan_mode = mode
        try:
            self.engine = ScanEngine(self.db, self.s, mode, roots or [], resume=resume)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Сканирование", f"Не удалось запустить сканирование:\n{exc}")
            return
        self._stopping = False
        self._last_refresh = self._scan_started = time.time()
        self.engine.start()
        self.progress.reset()
        self.results.set_scanning(True)
        self.poll.start()
        self._update_buttons()
        self.flash(("Продолжаем: " if resume else "") + MODE_NAMES.get(mode, mode) + " запущено", 4000)
        if self.current == "home":
            self.navigate("all")

    def update_index(self) -> None:
        roots = self.db.get_meta("last_roots")
        mode = self.db.get_meta("last_mode") or "full"
        if not roots:
            QMessageBox.information(self, "Обновить индекс", "Индекс ещё не создан — сначала запустите сканирование.")
            return
        present = {d.root.upper() for d in self._known_drives.values() if d.ready}
        alive = [r for r in roots if os.path.isdir(r) and (len(r) < 3 or r[1] != ":" or r[:3].upper() in present or not wu.IS_WIN)]
        skipped = [r for r in roots if r not in alive]
        if not alive:
            QMessageBox.information(self, "Обновить индекс", "Ни одно из ранее просканированных мест сейчас недоступно.")
            return
        if skipped:
            self.flash("Недоступны и пропущены: " + ", ".join(skipped), 6000)
        self.start_scan(mode, roots=alive)

    def _poll(self) -> None:
        eng = self.engine
        if eng is None:
            self.poll.stop()
            return
        p = eng.snapshot()
        if p.phase == "deep":
            self.lbl_phase.setText(f"Глубокий анализ: {fmt_int(p.deep_done)} из {fmt_int(p.deep_total)}")
        elif p.phase == "walk":
            pct = f" · {p.fraction * 100:.0f}%" if p.fraction is not None else ""
            self.lbl_phase.setText(f"{MODE_NAMES.get(p.mode, 'Сканирование')}{pct}")
        self.lbl_dir.setText(p.current)
        self.lbl_dirs.setText(f"Папок: {fmt_int(p.dirs)}")
        self.lbl_files.setText(f"Найдено: {fmt_int(p.files)}")
        unit = "файл/с" if p.phase == "walk" else "файл/с (анализ)"
        self.lbl_speed.setText(f"Скорость: {fmt_int(int(p.speed))} {unit}" if p.speed else "Скорость: —")
        self._set_errors_label(p.errors, p.links_skipped)
        self.progress.set_fraction(p.fraction)
        if eng.finished:
            self._scan_finished(p)
            return
        now = time.time()
        interval = 1.0 if now - self._scan_started < 10 else max(3.0, self.results.last_duration * 4)
        if now - self._last_refresh > interval:
            self._last_refresh = now
            if self.current == "home":
                self.home.refresh()
            elif self.current != "settings":
                self.results.refresh(keep_scroll=True)

    def _set_errors_label(self, n: int, links: int = 0) -> None:
        parts = []
        if n:
            parts.append(f"⚠ Недоступно: {fmt_int(n)}")
        if links:
            parts.append(f"ссылок пропущено: {fmt_int(links)}")
        self.lbl_errors.setText("  ·  ".join(parts))
        self.lbl_errors.setStyleSheet(f"color: {theme.palette()['warn']}; font-weight: 600;" if n else "")
        self.lbl_errors.setToolTip("Нажмите, чтобы увидеть, что не удалось просканировать и почему" if n else "")

    def _scan_finished(self, p) -> None:
        self.poll.stop()
        self.results.set_scanning(False)
        elapsed = fmt_duration(p.elapsed)
        if p.phase == "failed":
            QMessageBox.warning(self, "Сканирование", f"Сканирование завершилось ошибкой:\n{p.error_text}\n\n"
                                "Уже найденное сохранено в индексе.")
            msg = "Сканирование прервано из-за ошибки"
        elif p.phase == "stopped":
            msg = f"Остановлено · папок {fmt_int(p.dirs)}, файлов {fmt_int(p.files)} · можно продолжить кнопкой «Продолжить»"
        else:
            msg = (f"Готово за {elapsed} · папок {fmt_int(p.dirs)}, файлов {fmt_int(p.files)}"
                   + (f" · недоступно объектов: {fmt_int(p.errors)}" if p.errors else ""))
        self.progress.set_fraction(1.0 if p.phase == "done" else p.fraction or 0.0)
        QTimer.singleShot(6000, lambda: None if self.busy else self.progress.reset())
        self.lbl_phase.setText(msg)
        self.lbl_dir.setText("")
        self.lbl_speed.setText("")
        self._set_errors_label(p.errors, p.links_skipped)
        self._update_buttons()
        self._refresh_view()
        self.home.refresh()
        self.settings_page.update_info()
        if p.errors and p.phase == "done":
            self.flash(f"Часть объектов недоступна ({fmt_int(p.errors)}). Нажмите «⚠ Недоступно» внизу для подробностей.", 8000)

    def _idle_status(self) -> None:
        if self.busy:
            return
        self.lbl_phase.setText("Готово к работе")
        self.lbl_speed.setText("")
        self.progress.reset()
        if self._idle_thread and self._idle_thread.isRunning():
            return
        t = TaskThread(lambda prog, stop: self.db.totals(), self)
        t.done.connect(self._apply_idle)
        self._idle_thread = t
        t.start()

    def _apply_idle(self, t: dict) -> None:
        if self.busy:
            return
        self.lbl_dir.setText(f"В индексе: {fmt_int(t['files'])} файлов, {human_size(t['size'])}" if t["files"] else
                             "Индекс пуст — выберите диски и нажмите «Сканировать»")
        self.lbl_dirs.setText(f"Папок: {fmt_int(t['dirs'])}")
        self.lbl_files.setText(f"Найдено: {fmt_int(t['files'])}")
        self._set_errors_label(t["errors"])

    def flash(self, text: str, ms: int = 5000) -> None:
        """Временное сообщение в нижней панели."""
        if self.busy:
            self.lbl_dir.setText(text)
            return
        self.lbl_dir.setText(text)
        self._flash_timer.start(ms)

    def _update_buttons(self) -> None:
        busy = self.busy
        if self.scanning and not self._stopping:
            self.btn_scan.setText("Остановить")
            self.btn_scan.icon_kind = "stop"
            self.btn_scan.set_variant("danger")
            self.btn_scan.setEnabled(True)
        elif busy:
            self.btn_scan.setText("Остановить" if not self._stopping else "Остановка…")
            self.btn_scan.icon_kind = "stop"
            self.btn_scan.set_variant("danger")
        else:
            n = len(self.s.scan_roots)
            self.btn_scan.setText("Сканировать")
            self.btn_scan.icon_kind = "scan"
            self.btn_scan.set_variant("primary")
            self.btn_scan.setEnabled(True)
            self._stopping = False
        self.btn_drives.setText(f"Диски ({len(self.s.scan_roots)})" if self.mode.currentData() != "quick" else "Диски")
        self.btn_drives.setEnabled(not busy and self.mode.currentData() != "quick")
        self.btn_drives.setToolTip("В быстром режиме сканируются каталоги пользователя (см. «Настройки»)"
                                   if self.mode.currentData() == "quick" else "Выбор дисков и папок")
        self.mode.setEnabled(not busy)
        self.btn_update.setEnabled(not busy)
        self.btn_resume.setVisible(not busy and self.db.can_resume())
        self.btn_scan.update()

    # =================================================================== дубликаты
    def find_duplicates(self) -> None:
        if self.busy:
            return
        det = DuplicateDetector(self.db, self.s)
        total = det.pending_hash_count()
        if total == 0:
            self.flash("Все кандидаты уже сравнены по SHA-256")
            self.results.refresh()
            return

        def work(progress, stop):
            det.stop = stop
            return det.compute_hashes(lambda a, b, cur: progress(cur, a, b))
        t = TaskThread(work, self)
        t.progress.connect(self._dup_progress)
        t.done.connect(self._dup_done)
        t.failed.connect(lambda m: (QMessageBox.warning(self, "Дубликаты", f"Ошибка поиска дубликатов:\n{m}"),
                                    self._dup_finished()))
        self.dup_task = t
        self._stopping = False
        self.lbl_phase.setText("Поиск дубликатов (SHA-256)")
        self.progress.set_fraction(0.0)
        self._update_buttons()
        t.start()

    def _dup_progress(self, cur: str, a: int, b: int) -> None:
        self.lbl_phase.setText(f"SHA-256: {fmt_int(a)} из {fmt_int(b)}")
        self.lbl_dir.setText(cur)
        self.progress.set_fraction(a / b if b else None)

    def _dup_done(self, res) -> None:
        done, errs = res
        self.flash(f"Хеши посчитаны: {fmt_int(done)}" + (f", не прочитано: {fmt_int(errs)}" if errs else ""), 6000)
        self._dup_finished()

    def _dup_finished(self) -> None:
        self.progress.set_fraction(1.0)
        self.dup_task = None
        self._update_buttons()
        QTimer.singleShot(0, self._idle_status)
        self.results.refresh()
        self.home.refresh()

    # =================================================================== прочее
    def show_errors(self) -> None:
        ErrorsDialog(self.db, self).exec()

    def restart_admin(self) -> None:
        r = QMessageBox.question(self, "Права администратора",
                                 "Программа будет перезапущена с запросом UAC. Защита Windows не отключается: "
                                 "просто станут доступны каталоги, открытые для администраторов.\n\nПерезапустить?")
        if r == QMessageBox.Yes:
            if wu.restart_as_admin():
                QApplication.quit()
            else:
                QMessageBox.information(self, "Права администратора", "Запрос не был подтверждён или недоступен на этой ОС.")

    def _index_cleared(self) -> None:
        self.results.refresh()
        self.home.refresh()
        self._update_buttons()
        self._idle_status()

    def apply_theme(self, name: str) -> None:
        c = theme.palette(name)
        from utils import app_data_dir
        QApplication.instance().setStyleSheet(theme.qss(c, icons.make_check_png(str(app_data_dir() / 'check.png'))))
        self.results.err.setStyleSheet(f"color: {c['danger']};")
        for w in self.findChildren(QWidget):
            w.update()
        self.results.table.viewport().update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        compact = self.width() < 1040
        target = 70 if compact else 214
        if target != self._side_w:
            self._side_w = target
            for b in self.nav.values():
                b.set_compact(compact)
            self.brand_name.setVisible(not compact)
            self.ver.setVisible(not compact)
            if widgets.ANIMATE:
                anim = QVariantAnimation(self)
                anim.setDuration(180)
                anim.setStartValue(self.sidebar.width())
                anim.setEndValue(target)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                anim.valueChanged.connect(lambda v: self.sidebar.setFixedWidth(int(v)))
                anim.start(QVariantAnimation.DeleteWhenStopped)
            else:
                self.sidebar.setFixedWidth(target)

    def closeEvent(self, e):
        if self.scanning:
            r = QMessageBox.question(self, "Выход", "Идёт сканирование. Остановить его и выйти?\n"
                                     "Прогресс сохранится — позже можно нажать «Продолжить».")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.engine.stop()
            self.engine.join(20)
        if self.dup_task and self.dup_task.isRunning():
            self.dup_task.stop_event.set()
            self.dup_task.wait(5000)
        self.poller.stop()
        self.results.shutdown()
        self.s.save()
        super().closeEvent(e)
