"""Сборка DeepSearch в EXE с помощью PyInstaller.

Использование:
    python build_exe.py              # папка dist/DeepSearch/ (быстрый запуск, рекомендуется)
    python build_exe.py --onefile    # один файл dist/DeepSearch.exe
    python build_exe.py --admin      # запрашивать права администратора при каждом запуске (UAC)
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

EXCLUDES = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQml",
            "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtPdf",
            "PySide6.QtDataVisualization", "PySide6.QtSql", "PySide6.QtNetwork", "PySide6.QtBluetooth",
            "tkinter", "unittest", "pytest"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onefile", action="store_true", help="собрать один EXE-файл")
    ap.add_argument("--admin", action="store_true", help="всегда запрашивать права администратора (UAC)")
    args = ap.parse_args()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller не установлен. Выполните: pip install -r requirements-build.txt")
        return 1

    icon = os.path.join(ROOT, "assets", "icon.ico")
    if not os.path.exists(icon):
        print("Создаю иконку…")
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "make_icon.py")])

    cmd = [sys.executable, "-m", "PyInstaller", os.path.join(ROOT, "main.py"), "--noconfirm", "--clean",
           "--windowed", "--name", "DeepSearch", "--icon", icon,
           "--add-data", f"{os.path.join(ROOT, 'assets')}{os.pathsep}assets",
           "--paths", ROOT, "--hidden-import", "pypdf",
           "--distpath", os.path.join(ROOT, "dist"), "--workpath", os.path.join(ROOT, "build"),
           "--specpath", os.path.join(ROOT, "build")]
    for m in EXCLUDES:
        cmd += ["--exclude-module", m]
    if args.onefile:
        cmd.append("--onefile")
    if args.admin:
        cmd.append("--uac-admin")
    print(" ".join(cmd))
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc == 0:
        target = "dist\\DeepSearch.exe" if args.onefile else "dist\\DeepSearch\\DeepSearch.exe"
        print(f"\nГотово: {target}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
