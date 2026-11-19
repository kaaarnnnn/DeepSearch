"""Сквозная проверка интерфейса (offscreen): сканирование, поиск, сортировка, дубликаты, экспорт, диалоги."""
import json
import os
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, sec=0.3, until=None, timeout=30):
    t = time.time()
    while time.time() - t < (timeout if until else sec):
        app.processEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return not until


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEARCH_HOME", str(tmp_path / "home"))
    root = tmp_path / "data"
    for sub in ("a", "b", "c"):
        (root / sub).mkdir(parents=True)
    for i in range(40):
        (root / "abc"[i % 3] / f"file{i}.txt").write_text("text " * (i % 4 + 1))
    (root / "a" / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "b" / "report.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "c" / "договор.txt").write_text("договор аренды помещения", encoding="utf-8")
    from database import Database
    from settings import Settings
    from ui import theme
    from ui.main_window import MainWindow
    app.setStyleSheet(theme.qss(theme.palette("dark")))
    st = Settings()
    st.scan_roots = [str(root)]
    w = MainWindow(Database(), st)
    w.root_dir = root
    w.show()
    yield w
    w.engine and w.engine.stop()
    w.close()


def finish_scan(app, w):
    assert pump(app, until=lambda: w.engine is not None and w.engine.finished, timeout=60)
    pump(app, 0.5)


def test_scan_search_sort_export(app, win, tmp_path):
    win.start_scan("deep", roots=[str(win.root_dir)])
    finish_scan(app, win)
    win.navigate("all")
    pump(app, until=lambda: win.results.model.rowCount() >= 43)
    assert win.results.model.rowCount() == 43
    # поиск по имени, по содержимому, по синтаксису
    win.search.setText("договор")
    pump(app, until=lambda: win.results.model.rowCount() == 1)
    assert win.results.model.rowCount() == 1
    win.search.setText("content:помещения")
    pump(app, until=lambda: win.results.model.rowCount() == 1 and win.results.model.row_at(0)[2] == "договор.txt")
    win.search.setText("ext:pdf")
    pump(app, until=lambda: win.results.model.rowCount() == 2)
    assert win.results.model.rowCount() == 2
    # сортировка заголовком по размеру
    win.search.setText("")
    pump(app, until=lambda: win.results.model.rowCount() == 43)
    win.results._header_clicked(3)
    pump(app, 0.6)
    sizes = [win.results.model.row_at(i)[4] for i in range(win.results.model.rowCount())]
    assert sizes == sorted(sizes, reverse=True)
    # экспорт
    out = tmp_path / "out.json"
    from exporter import export_results
    n = export_results(win.results.engine, win.results.build_filter(), "name", False, str(out), "json")
    assert n == 43 and len(json.loads(out.read_text(encoding="utf-8"))) == 43
    # диалоги создаются без ошибок
    from ui.dialogs import ErrorsDialog, PropertiesDialog
    row = win.results.model.row_at(0)
    d = PropertiesDialog(row, win)
    assert row[2] in d.windowTitle()
    d.close()
    ErrorsDialog(win.db, win).close()
    # дубликаты
    win.navigate("duplicates")
    pump(app, until=lambda: win.results.model.rowCount() >= 2)
    assert win.results.model.rowCount() >= 2
    # фильтр-панель и некорректный синтаксис
    win.navigate("all")
    win.search.setText("size:abc")
    pump(app, 0.6)
    assert not win.results.err.isHidden()
    win.search.setText("")
    pump(app, 0.5)


def test_duplicates_button_flow(app, win):
    win.start_scan("full", roots=[str(win.root_dir)])
    finish_scan(app, win)
    win.navigate("duplicates")
    pump(app, 0.8)
    win.find_duplicates()
    assert pump(app, until=lambda: win.dup_task is None, timeout=30)
    pump(app, 0.8)
    assert any(win.results.model.row_at(i)[2] == "report.pdf" for i in range(win.results.model.rowCount()))


def test_stop_resume_update_theme(app, win, tmp_path):
    root = tmp_path / "many"
    for i in range(80):
        d = root / f"d{i}" / "x"
        d.mkdir(parents=True)
        for j in range(4):
            (d / f"f{j}.txt").write_text("z")
    win.s.threads = 1
    win.start_scan("full", roots=[str(root)])
    pump(app, until=lambda: win.engine.progress.dirs >= 6 or win.engine.finished, timeout=20)
    win._scan_clicked()                         # «Остановить»
    finish_scan(app, win)
    if win.engine.progress.phase == "stopped":
        assert not win.btn_resume.isHidden() or win.db.can_resume()
        win.start_scan(resume=True)
        finish_scan(app, win)
    assert win.db.totals()["files"] >= 320
    assert not win.db.can_resume()
    (root / "d0" / "x" / "new.txt").write_text("n")
    win.update_index()
    finish_scan(app, win)
    assert win.db.totals()["files"] >= 321
    win.apply_theme("light")
    win.apply_theme("dark")
    win.nav["settings"].click()
    pump(app, 0.3)
    win._show_drives()
    win.drive_popup.hide()
