"""Генерирует assets/icon.ico и assets/icon.png из векторного рисунка приложения (нужен PySide6)."""
import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from ui.icons import app_icon_pixmap  # noqa: E402


def png_bytes(size: int) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    app_icon_pixmap(size).save(buf, "PNG")
    return bytes(ba)


def main():
    app = QGuiApplication([])  # noqa: F841
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [png_bytes(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, img in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(img), offset)
        offset += len(img)
    os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
    with open(os.path.join(ROOT, "assets", "icon.ico"), "wb") as fh:
        fh.write(header + entries + b"".join(images))
    with open(os.path.join(ROOT, "assets", "icon.png"), "wb") as fh:
        fh.write(png_bytes(256))
    print("Иконки созданы в assets/")


if __name__ == "__main__":
    main()
