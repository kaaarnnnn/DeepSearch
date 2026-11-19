"""Модель таблицы результатов (виртуальная, постраничная) и фоновый запуск поиска."""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor

import filetypes as ft
from filters import FileFilter
from scanner import ACCESS_OK, ACCESS_TEXT
from search import SearchEngine, SearchResult
from ui.icons import file_icon
from ui.theme import palette
from utils import fmt_ts, human_size

COLUMNS = ["Название", "Формат", "Полный путь", "Размер", "Создан", "Изменён", "Тип", "Атрибуты", "Доступность",
           "SHA-256"]
COL_SORT = {0: "name", 1: "ext", 2: "path", 3: "size", 4: "ctime", 5: "mtime", 6: "type"}
SORT_COL = {v: k for k, v in COL_SORT.items()}
PAGE = 200
MAX_PAGES = 60

# индексы полей строки (см. search.COLUMNS)
R_ID, R_PATH, R_NAME, R_EXT, R_SIZE, R_CTIME, R_MTIME, R_CAT, R_FLAGS, R_ACCESS, R_SHA, R_SIG, R_MISMATCH = range(13)


def type_text(row: tuple) -> str:
    t = ft.CAT_LABELS.get(row[R_CAT], row[R_CAT])
    if row[R_SIG]:
        t += f" · {row[R_SIG]}"
    if row[R_MISMATCH]:
        t += " ⚠ расширение не совпадает с содержимым"
    return t


def attrs_text(flags: int) -> str:
    parts = []
    if flags & ft.F_HIDDEN:
        parts.append("Скрытый")
    if flags & ft.F_SYSTEM:
        parts.append("системный" if parts else "Системный")
    if flags & ft.F_READONLY:
        parts.append("только чтение" if parts else "Только чтение")
    if flags & ft.F_LINK:
        parts.append("ссылка" if parts else "Ссылка")
    return ", ".join(parts) if parts else "Обычный"


class FileTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.result: Optional[SearchResult] = None
        self._pages: OrderedDict[int, list] = OrderedDict()
        self.dup_mode = False
        self.present_drives: Optional[set[str]] = None

    # ---- данные
    def set_result(self, result: Optional[SearchResult], dup_mode: bool = False) -> None:
        self.beginResetModel()
        old, self.result = self.result, result
        self.dup_mode = dup_mode
        self._pages.clear()
        self.endResetModel()
        if old:
            old.close()

    def row_at(self, row: int) -> Optional[tuple]:
        if not self.result or row < 0 or row >= self.result.count:
            return None
        pg = row // PAGE
        page = self._pages.get(pg)
        if page is None:
            page = self.result.fetch(pg * PAGE, PAGE)
            self._pages[pg] = page
            while len(self._pages) > MAX_PAGES:
                self._pages.popitem(last=False)
        else:
            self._pages.move_to_end(pg)
        i = row - pg * PAGE
        return page[i] if i < len(page) else None

    # ---- Qt API
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() or not self.result else self.result.count

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def _access_text(self, row: tuple) -> str:
        if self.present_drives is not None and len(row[R_PATH]) > 2 and row[R_PATH][1] == ":":
            if row[R_PATH][:3].upper() not in self.present_drives:
                return "Диск отключён"
        return ACCESS_TEXT.get(row[R_ACCESS], "?")

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.row_at(index.row())
        if row is None:
            return "…" if role == Qt.DisplayRole and index.column() == 0 else None
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return row[R_NAME]
            if col == 1:
                return row[R_EXT].upper() if row[R_EXT] else "—"
            if col == 2:
                return row[R_PATH]
            if col == 3:
                return human_size(row[R_SIZE])
            if col == 4:
                return fmt_ts(row[R_CTIME])
            if col == 5:
                return fmt_ts(row[R_MTIME])
            if col == 6:
                return type_text(row)
            if col == 7:
                return attrs_text(row[R_FLAGS])
            if col == 8:
                return self._access_text(row)
            if col == 9:
                return row[R_SHA][:16] + "…" if row[R_SHA] else ""
        elif role == Qt.DecorationRole and col == 0:
            return file_icon(row[R_CAT])
        elif role == Qt.TextAlignmentRole:
            if col == 3:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        elif role == Qt.ForegroundRole:
            c = palette()
            if col == 8 and (row[R_ACCESS] != ACCESS_OK or self._access_text(row) == "Диск отключён"):
                return QBrush(QColor(c["danger"] if row[R_ACCESS] in (0, 4) else c["warn"]))
            if col == 6 and row[R_MISMATCH]:
                return QBrush(QColor(c["warn"]))
            if row[R_FLAGS] & ft.F_HIDDEN and col in (0, 1):
                return QBrush(QColor(c["muted"]))
        elif role == Qt.BackgroundRole and self.dup_mode and row[R_SHA]:
            h = int(row[R_SHA][:6], 16) % 360
            col_ = QColor.fromHsl(h, 120, 105 if palette()["bg"].startswith("#0") else 205, 70)
            return QBrush(col_)
        elif role == Qt.ToolTipRole:
            if col in (0, 2):
                return row[R_PATH]
            if col == 6:
                return type_text(row)
            if col == 9 and row[R_SHA]:
                return row[R_SHA]
        return None


class SearchRunner(QObject):
    """Запускает поиск в отдельном потоке; устаревшие запросы прерываются."""
    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, engine: SearchEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._token = 0
        self._holder: list = []
        self._lock = threading.Lock()

    def run(self, flt: FileFilter, sort: str, desc: bool) -> int:
        with self._lock:
            self._token += 1
            token = self._token
            for conn in self._holder:                # прерываем предыдущий незавершённый запрос
                try:
                    conn.interrupt()
                except Exception:
                    pass
            self._holder = holder = []
        threading.Thread(target=self._work, args=(token, flt, sort, desc, holder), daemon=True).start()
        return token

    def _work(self, token, flt, sort, desc, holder):
        try:
            res = self.engine.run(flt, sort, desc, holder)
        except Exception as exc:  # noqa: BLE001
            if token == self._token:
                self.failed.emit(token, str(exc))
            return
        if token != self._token:
            res.close()
            return
        self.finished.emit(token, res)
