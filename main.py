"""DeepSearch — точка входа приложения."""
from __future__ import annotations

import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler

# Работа из любой текущей папки и из PyInstaller-сборки
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import APP_NAME, app_data_dir, resource_path  # noqa: E402


def setup_logging() -> None:
    handler = RotatingFileHandler(app_data_dir() / "deepsearch.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> int:
    setup_logging()
    log = logging.getLogger("deepsearch")
    sys.setswitchinterval(0.0005)        # отзывчивый интерфейс при активных потоках сканирования

    from PySide6.QtCore import QLockFile, Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox

    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)

    from settings import Settings
    from ui import icons, theme, widgets
    from ui.main_window import MainWindow
    from database import Database

    def excepthook(et, ev, tb):
        log.error("Необработанное исключение:\n%s", "".join(traceback.format_exception(et, ev, tb)))
        try:
            QMessageBox.critical(None, "DeepSearch — ошибка",
                                 f"Произошла непредвиденная ошибка:\n{ev}\n\nПодробности записаны в журнал:\n"
                                 f"{app_data_dir() / 'deepsearch.log'}")
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = excepthook

    lock = QLockFile(str(app_data_dir() / "deepsearch.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "DeepSearch", "DeepSearch уже запущен.")
        return 0

    settings = Settings.load()
    widgets.set_animations(settings.animations)
    c = theme.palette(settings.theme)
    app.setStyleSheet(theme.qss(c, icons.make_check_png(str(app_data_dir() / 'check.png'))))
    app.setWindowIcon(icons.app_icon())
    f = QFont("Segoe UI", 10)
    app.setFont(f)

    try:
        db = Database()
    except Exception as exc:  # noqa: BLE001
        log.exception("Не удалось открыть базу данных")
        QMessageBox.critical(None, "DeepSearch", f"Не удалось открыть индекс:\n{exc}\n\n"
                             f"Файл: {app_data_dir() / 'index.db'}")
        return 1

    win = MainWindow(db, settings)
    win.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
