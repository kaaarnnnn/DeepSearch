import json
import os
import time
import zipfile

import pytest

from database import Database
from duplicate_detector import DuplicateDetector
from exporter import export_results
from filters import FilterError, parse_date_range, parse_size, parse_size_range
from scanner import ScanEngine
from search import SearchEngine, parse_query
from settings import Settings


def make_docx(path, text):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("word/document.xml", f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")


@pytest.fixture
def tree(tmp_path):
    r = tmp_path / "root"
    (r / "docs").mkdir(parents=True)
    (r / "pics").mkdir()
    (r / "dups").mkdir()
    (r / "locked").mkdir()
    (r / "docs" / "договор аренды.txt").write_text("Договор аренды помещения. Арендатор обязуется платить.", encoding="utf-8")
    (r / "docs" / ".hidden.txt").write_text("secret", encoding="utf-8")
    (r / "docs" / "README").write_text("plain readme without extension", encoding="utf-8")
    make_docx(r / "docs" / "Отчёт.docx", "Квартальный отчёт по продажам")
    (r / "pics" / "photo.jpg").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)    # PNG под видом JPG
    (r / "pics" / "real.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x01" * 100)
    (r / "old.bak").write_bytes(b"x" * 10)
    (r / "dups" / "a.bin").write_bytes(b"same-content" * 1000)
    (r / "dups" / "b.dat").write_bytes(b"same-content" * 1000)
    (r / "dups" / "c.bin").write_bytes(b"differ-content" * 857)     # другой размер
    (r / "locked" / "x.txt").write_text("hidden from us")
    try:
        os.symlink(str(r), str(r / "docs" / "loop"), target_is_directory=True)   # цикл
    except OSError:
        pass
    return r


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEARCH_HOME", str(tmp_path / "data"))
    db = Database(tmp_path / "idx.db")
    st = Settings()
    st.threads = 4
    return db, st


def run_scan(db, st, mode, roots, resume=False):
    e = ScanEngine(db, st, mode, roots, resume)
    e.start()
    assert e.done_ev.wait(60)
    return e


def names(db, query="", **kw):
    eng = SearchEngine(db)
    res = eng.run(parse_query(query), **kw)
    try:
        return sorted(r[2] for r in res.fetch(0, 1000))
    finally:
        res.close()


def test_full_scan_finds_everything(env, tree):
    db, st = env
    e = run_scan(db, st, "full", [str(tree)])
    assert e.progress.phase == "done"
    found = names(db)
    for n in ("договор аренды.txt", ".hidden.txt", "README", "Отчёт.docx", "photo.jpg", "old.bak", "x.txt"):
        assert n in found
    assert "loop" not in found and found.count("README") == 1      # симлинк-цикл не разворачивается
    assert e.progress.links_skipped >= 1 or not hasattr(os, "symlink")


def test_search_filters(env, tree):
    db, st = env
    run_scan(db, st, "full", [str(tree)])
    assert names(db, "ДОГОВОР") == ["договор аренды.txt"]              # регистр кириллицы
    assert names(db, "*.png") == ["real.png"]
    assert names(db, "ext:txt") == [".hidden.txt", "x.txt", "договор аренды.txt"]
    assert names(db, "noext:yes") == ["README"]
    assert names(db, "hidden:yes") == [".hidden.txt"]
    assert names(db, "type:temp") == ["old.bak"]
    assert names(db, "size:10") == ["old.bak"] and names(db, "size:<=10") == [".hidden.txt", "old.bak"]
    assert names(db, "path:pics ext:png") == ["real.png"]
    assert names(db, "modified:today") != []
    assert names(db, "modified:<2000-01-01") == []
    assert names(db, "отчёт -ext:txt") == ["Отчёт.docx"]
    assert names(db, "a -ext:txt -ext:bin") != [] and "x.txt" not in names(db, "x -ext:txt")
    assert names(db, "re:^real") == ["real.png"]
    with pytest.raises(FilterError):
        parse_query("size:abc")


def test_sorting_and_paging(env, tree):
    db, st = env
    run_scan(db, st, "full", [str(tree)])
    eng = SearchEngine(db)
    res = eng.run(parse_query(""), "size", True)
    sizes = [r[4] for r in res.fetch(0, 100)]
    assert sizes == sorted(sizes, reverse=True)
    page2 = res.fetch(3, 2)
    assert len(page2) == 2 and page2[0][0] == res.fetch(0, 100)[3][0]
    res.close()


def test_incremental_and_removal(env, tree):
    db, st = env
    run_scan(db, st, "full", [str(tree)])
    conn = db.connect()
    first_ids = dict(conn.execute("SELECT name,id FROM files"))
    (tree / "old.bak").unlink()
    (tree / "docs" / "new.txt").write_text("new")
    time.sleep(0.05)
    run_scan(db, st, "full", [str(tree)])
    ids = dict(conn.execute("SELECT name,id FROM files"))
    assert "old.bak" not in ids and "new.txt" in ids
    assert ids["README"] == first_ids["README"]            # неизменённые строки не пересоздавались
    import shutil
    shutil.rmtree(tree / "pics")                           # исчезла целая папка
    run_scan(db, st, "full", [str(tree)])
    assert "real.png" not in names(db)
    conn.close()


def test_access_errors_reported_and_scan_continues(env, tree, monkeypatch):
    db, st = env
    real = os.scandir

    def fake(p):
        if "locked" in str(p):
            raise PermissionError(13, "denied")
        return real(p)
    monkeypatch.setattr("scanner.os.scandir", fake)
    e = run_scan(db, st, "full", [str(tree)])
    assert e.progress.errors >= 1 and e.progress.phase == "done"
    errs = db.errors()
    assert any(k == "access" and "locked" in p for p, k, _, _ in errs)
    assert "договор аренды.txt" in names(db)


def test_deep_scan_signatures_hashes_content(env, tree):
    db, st = env
    e = run_scan(db, st, "deep", [str(tree)])
    assert e.progress.phase == "done" and e.progress.deep_done > 0
    eng = SearchEngine(db)
    mm = [r[2] for r in eng.run(parse_query("mismatch:yes")).fetch(0, 50)]
    assert "photo.jpg" in mm and "real.png" not in mm
    assert names(db, "content:аренды") == ["договор аренды.txt"]
    assert names(db, "content:квартальный") == ["Отчёт.docx"]
    assert names(db, "content:продажам -ext:txt") == ["Отчёт.docx"]
    assert names(db, "content:readme") == ["README"]
    conn = db.connect()
    sha = dict(conn.execute("SELECT name, sha256 FROM files WHERE name IN ('a.bin','b.dat','c.bin','README')"))
    assert sha["a.bin"] and sha["a.bin"] == sha["b.dat"] and sha["c.bin"] is None and sha["README"] is None
    assert names(db, "dup:yes") == ["a.bin", "b.dat"]
    s = DuplicateDetector(db, st).summary()
    assert s["groups"] == 1 and s["files"] == 2 and s["wasted"] == 12000
    conn.close()


def test_duplicate_detector_standalone(env, tree):
    db, st = env
    run_scan(db, st, "full", [str(tree)])
    det = DuplicateDetector(db, st)
    assert det.summary()["groups"] == 0 and det.pending_hash_count() >= 2
    done, errs = det.compute_hashes()
    assert errs == 0 and done >= 2
    g = det.groups()
    assert len(g) == 1 and g[0][1] == 2


def test_resume_after_stop(env, tmp_path):
    db, st = env
    root = tmp_path / "big"
    for i in range(60):
        d = root / f"d{i}" / "sub"
        d.mkdir(parents=True)
        for j in range(5):
            (d / f"f{j}.txt").write_text("x")
    st.threads = 1
    e = ScanEngine(db, st, "full", [str(root)])
    e.start()
    while e.progress.dirs < 10 and not e.finished:
        time.sleep(0.005)
    e.stop()
    assert e.done_ev.wait(30)
    if e.progress.phase == "stopped":
        assert db.can_resume()
        n1 = db.totals()["files"]
        e2 = run_scan(db, st, "full", [], resume=True)
        assert e2.progress.phase == "done"
        assert db.totals()["files"] == 300 and db.totals()["files"] >= n1
        assert not db.can_resume()
    else:
        assert db.totals()["files"] == 300


def test_export(env, tree, tmp_path):
    db, st = env
    run_scan(db, st, "full", [str(tree)])
    eng = SearchEngine(db)
    out_csv, out_json = tmp_path / "o.csv", tmp_path / "o.json"
    n = export_results(eng, parse_query("ext:txt"), "name", False, str(out_csv), "csv")
    assert n == 3 and out_csv.read_text(encoding="utf-8-sig").count("\n") == 4
    export_results(eng, parse_query("ext:txt"), "name", False, str(out_json), "json")
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert len(data) == 3 and {"name", "path", "size_bytes", "sha256"} <= set(data[0])


def test_parsers():
    assert parse_size("1.5gb") == int(1.5 * 1024 ** 3) and parse_size("10 мб") == 10 * 1024 ** 2
    assert parse_size_range("1mb..5mb") == (1048576, 5 * 1048576)
    assert parse_size_range(">10kb") == (10241, None)
    lo, hi = parse_date_range("2024-05")
    assert hi > lo
    assert parse_date_range("7d")[0] is not None
    f = parse_query('c:\\users\\anna "program files" ext:.PDF')
    assert f.paths == ["c:\\users\\anna"] and f.terms == ["program files"] and f.exts == ["pdf"]
