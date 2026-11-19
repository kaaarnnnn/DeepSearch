"""Векторные иконки, нарисованные через QPainter (никаких внешних файлов), и значки типов файлов."""
from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF)

from filetypes import CAT_COLORS


def _pen(color: QColor, w: float) -> QPen:
    p = QPen(color, w)
    p.setCapStyle(Qt.RoundCap)
    p.setJoinStyle(Qt.RoundJoin)
    return p


def draw_glyph(p: QPainter, kind: str, r: QRectF, color: QColor) -> None:
    """Рисует глиф внутри квадрата r (контурный стиль)."""
    p.save()
    s = min(r.width(), r.height())
    x0, y0 = r.center().x() - s / 2, r.center().y() - s / 2
    p.translate(x0, y0)
    p.scale(s / 24.0, s / 24.0)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(_pen(color, 1.8))
    p.setBrush(Qt.NoBrush)
    P = QPointF

    def line(*pts):
        p.drawPolyline(QPolygonF([P(*a) for a in pts]))

    k = kind
    if k == "home":
        line((3.5, 11.5), (12, 4), (20.5, 11.5))
        line((6, 10), (6, 20), (18, 20), (18, 10))
        line((10, 20), (10, 14.5), (14, 14.5), (14, 20))
    elif k == "files":
        p.drawRoundedRect(QRectF(7.5, 3.5, 12, 14), 2, 2)
        line((4.5, 7), (4.5, 20.5), (15, 20.5))
    elif k == "documents":
        line((6, 3.5), (14, 3.5), (19, 8.5), (19, 20.5), (6, 20.5), (6, 3.5))
        line((14, 3.5), (14, 8.5), (19, 8.5))
        line((9, 13), (16, 13))
        line((9, 16.5), (16, 16.5))
    elif k == "images":
        p.drawRoundedRect(QRectF(3.5, 5, 17, 14), 2.5, 2.5)
        line((4, 17), (9, 12), (13, 16), (16, 13), (20, 17))
        p.drawEllipse(P(15.5, 9.2), 1.4, 1.4)
    elif k == "video":
        p.drawRoundedRect(QRectF(3.5, 5.5, 17, 13), 2.5, 2.5)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([P(10.2, 9.3), P(10.2, 14.7), P(14.8, 12)]))
    elif k == "audio":
        p.drawEllipse(P(8, 17), 2.6, 2.6)
        line((10.6, 17), (10.6, 5), (18, 7.5), (18, 14.5))
        p.drawEllipse(P(15.4, 14.8), 2.6, 2.6)
    elif k == "archives":
        p.drawRoundedRect(QRectF(4.5, 3.5, 15, 17), 2.5, 2.5)
        for yy in (6.5, 9.5, 12.5):
            line((12, yy), (12, yy + 1.2))
        p.drawRoundedRect(QRectF(10, 15, 4, 3.5), 1, 1)
    elif k == "apps":
        for (a, b) in ((4, 4), (13.5, 4), (4, 13.5), (13.5, 13.5)):
            p.drawRoundedRect(QRectF(a, b, 6.5, 6.5), 1.6, 1.6)
    elif k == "duplicates":
        p.drawRoundedRect(QRectF(8.5, 8.5, 12, 12), 2.2, 2.2)
        line((15.5, 8.5), (15.5, 5.5), (15.5, 5), (5.5, 5), (3.5, 6.5), (3.5, 15.5), (8.5, 15.5))
        line((3.5, 6), (3.5, 5.2))
    elif k == "settings":
        p.drawEllipse(P(12, 12), 3.2, 3.2)
        for i in range(8):
            a = math.radians(i * 45)
            line((12 + 6 * math.cos(a), 12 + 6 * math.sin(a)), (12 + 8.6 * math.cos(a), 12 + 8.6 * math.sin(a)))
        p.drawEllipse(P(12, 12), 6.2, 6.2)
    elif k == "search":
        p.drawEllipse(P(10.5, 10.5), 6.2, 6.2)
        line((15.2, 15.2), (20.5, 20.5))
    elif k == "scan":
        p.drawEllipse(P(10.5, 10.5), 6.2, 6.2)
        line((15.2, 15.2), (20.5, 20.5))
        line((10.5, 7.8), (10.5, 13.2))
        line((7.8, 10.5), (13.2, 10.5))
    elif k == "stop":
        p.setBrush(color)
        p.drawRoundedRect(QRectF(6.5, 6.5, 11, 11), 2, 2)
    elif k == "play":
        p.setBrush(color)
        p.drawPolygon(QPolygonF([P(8, 5.5), P(8, 18.5), P(18.5, 12)]))
    elif k == "refresh":
        path = QPainterPath()
        path.arcMoveTo(QRectF(4.5, 4.5, 15, 15), 40)
        path.arcTo(QRectF(4.5, 4.5, 15, 15), 40, 280)
        p.drawPath(path)
        line((18.5, 3.5), (18.5, 8.5), (13.5, 8.5))
    elif k == "folder":
        line((3.5, 7), (3.5, 19), (20.5, 19), (20.5, 8.5), (11, 8.5), (9.5, 6), (4.5, 6), (3.5, 7))
    elif k == "drive":
        p.drawRoundedRect(QRectF(3.5, 8, 17, 8.5), 2.5, 2.5)
        p.setBrush(color)
        p.drawEllipse(P(16.8, 12.2), 1, 1)
        line((6.5, 12.2), (11, 12.2))
    elif k == "history":
        path = QPainterPath()
        path.arcMoveTo(QRectF(4, 4, 16, 16), 90)
        path.arcTo(QRectF(4, 4, 16, 16), 90, 300)
        p.drawPath(path)
        line((12, 7.8), (12, 12.4), (15.2, 14.2))
        line((3, 4.5), (5.2, 7.8), (8.6, 6.4))
    elif k == "export":
        line((12, 15), (12, 4))
        line((8, 7.8), (12, 3.8), (16, 7.8))
        line((4.5, 13), (4.5, 20), (19.5, 20), (19.5, 13))
    elif k == "copy":
        p.drawRoundedRect(QRectF(8.5, 8.5, 12, 12), 2.2, 2.2)
        line((15.5, 8.5), (15.5, 5.5), (5.5, 5.5), (3.5, 7), (3.5, 15.5), (8.5, 15.5))
    elif k == "open":
        line((13, 4), (20, 4), (20, 11))
        line((20, 4), (11, 13))
        line((17, 14.5), (17, 19), (5, 19), (5, 7), (9.5, 7))
    elif k == "location":
        path = QPainterPath()
        path.moveTo(12, 21)
        path.cubicTo(5, 14, 5.5, 4, 12, 4)
        path.cubicTo(18.5, 4, 19, 14, 12, 21)
        p.drawPath(path)
        p.drawEllipse(P(12, 10.5), 2.4, 2.4)
    elif k == "info":
        p.drawEllipse(P(12, 12), 8.5, 8.5)
        line((12, 11), (12, 16.5))
        p.setBrush(color)
        p.drawEllipse(P(12, 7.9), 0.4, 0.4)
    elif k == "alert":
        line((12, 3.8), (21, 19.5), (3, 19.5), (12, 3.8))
        line((12, 9.5), (12, 14))
        p.setBrush(color)
        p.drawEllipse(P(12, 16.9), 0.35, 0.35)
    elif k == "chart":
        for i, h in enumerate((7, 12, 9, 16)):
            p.drawRoundedRect(QRectF(4 + i * 4.6, 20 - h, 3, h), 1, 1)
    elif k == "bolt":
        p.drawPolygon(QPolygonF([P(13.5, 3), P(5.5, 13.5), P(11.5, 13.5), P(10.5, 21), P(18.5, 10), P(12.5, 10)]))
    elif k == "layers":
        line((12, 4), (21, 9), (12, 14), (3, 9), (12, 4))
        line((3, 13), (12, 18), (21, 13))
    elif k == "code":
        line((9, 7), (4, 12), (9, 17))
        line((15, 7), (20, 12), (15, 17))
        line((13.2, 5.5), (10.8, 18.5))
    elif k == "clock":
        p.drawEllipse(P(12, 12), 8, 8)
        line((12, 7), (12, 12), (15.5, 14))
    elif k == "shield":
        line((12, 3.5), (19.5, 6.5), (19.5, 12.5))
        line((19.5, 12.5), (12, 20.5), (4.5, 12.5), (4.5, 6.5), (12, 3.5))
    elif k == "trash":
        pass
    p.restore()


@lru_cache(maxsize=256)
def glyph_icon(kind: str, color_hex: str, size: int = 20) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    draw_glyph(p, kind, QRectF(0, 0, size, size), QColor(color_hex))
    p.end()
    return QIcon(pm)


_CAT_GLYPH = {"documents": "documents", "images": "images", "video": "video", "audio": "audio",
              "archives": "archives", "apps": "apps", "code": "code", "system": "settings", "temp": "clock",
              "other": None}


@lru_cache(maxsize=64)
def file_icon(category: str) -> QIcon:
    """Значок файла в цвете категории: страница с загнутым углом и символом типа."""
    n = 64
    pm = QPixmap(n * 2, n * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    col = QColor(CAT_COLORS.get(category, "#6B7385"))
    fold = 16
    left, top, right, bottom = 12, 5, 52, 59
    page = QPainterPath()
    page.moveTo(left + 4, top)
    page.lineTo(right - fold, top)
    page.lineTo(right, top + fold)
    page.lineTo(right, bottom - 4)
    page.quadTo(right, bottom, right - 4, bottom)
    page.lineTo(left + 4, bottom)
    page.quadTo(left, bottom, left, bottom - 4)
    page.lineTo(left, top + 4)
    page.quadTo(left, top, left + 4, top)
    g = QLinearGradient(0, top, 0, bottom)
    g.setColorAt(0, col.lighter(125))
    g.setColorAt(1, col.darker(115))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawPath(page)
    corner = QPainterPath()
    corner.moveTo(right - fold, top)
    corner.lineTo(right - fold, top + fold)
    corner.lineTo(right, top + fold)
    corner.closeSubpath()
    p.setBrush(QColor(255, 255, 255, 90))
    p.drawPath(corner)
    glyph = _CAT_GLYPH.get(category)
    if glyph:
        draw_glyph(p, glyph, QRectF(19, 24, 26, 26), QColor(255, 255, 255, 235))
    p.end()
    return QIcon(pm)


def app_icon_pixmap(size: int = 256) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#5B9BFF"))
    g.setColorAt(1, QColor("#7C5CFF"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    m = size * 0.04
    p.drawRoundedRect(QRectF(m, m, size - 2 * m, size - 2 * m), size * 0.22, size * 0.22)
    # страница
    page = QPainterPath()
    pl, pt, pr, pb = size * 0.27, size * 0.18, size * 0.67, size * 0.78
    f = size * 0.14
    page.moveTo(pl, pt)
    page.lineTo(pr - f, pt)
    page.lineTo(pr, pt + f)
    page.lineTo(pr, pb)
    page.lineTo(pl, pb)
    page.closeSubpath()
    p.setBrush(QColor(255, 255, 255, 60))
    p.drawPath(page)
    # лупа
    pen = QPen(QColor("white"), size * 0.065)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(QColor(255, 255, 255, 40))
    c = QPointF(size * 0.5, size * 0.5)
    rad = size * 0.2
    p.drawEllipse(c, rad, rad)
    p.drawLine(QPointF(c.x() + rad * 0.72, c.y() + rad * 0.72), QPointF(size * 0.78, size * 0.78))
    p.end()
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(app_icon_pixmap(s))
    return ic


def make_check_png(path: str) -> str:
    """Галочка для QCheckBox (QSS принимает только файл-изображение)."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(_pen(QColor("white"), 3.4))
    p.drawPolyline(QPolygonF([QPointF(8, 17), QPointF(14, 23), QPointF(24, 9)]))
    p.end()
    pm.save(path, "PNG")
    return path.replace("\\", "/")
