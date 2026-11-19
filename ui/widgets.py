"""Собственные виджеты: анимированные кнопки, боковая панель, прогресс, диаграммы, карточки, фоновые задачи."""
from __future__ import annotations

import math
import threading
from typing import Callable, Optional

from PySide6.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QThread, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from ui.icons import draw_glyph
from ui.theme import palette


ANIMATE = True      # глобальный переключатель анимаций (настройка «Плавные анимации»)


def set_animations(on: bool) -> None:
    global ANIMATE
    ANIMATE = bool(on)


def lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


def fade_in(widget: QWidget, ms: int = 200) -> None:
    """Плавное появление виджета (эффект снимается по окончании, чтобы не тормозить таблицы)."""
    if not ANIMATE:
        return
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeleteWhenStopped)
    widget._fade_anim = anim


class _Hoverable:
    """Плавный прогресс наведения 0..1."""

    def _init_hover(self, duration: int = 140):
        self._hover = 0.0
        self._hanim = QVariantAnimation(self)
        self._hanim.setDuration(duration)
        self._hanim.valueChanged.connect(self._on_hover)

    def _on_hover(self, v):
        self._hover = float(v)
        self.update()

    def _animate_to(self, target: float):
        self._hanim.stop()
        self._hanim.setStartValue(self._hover)
        self._hanim.setEndValue(target)
        self._hanim.start()


class AnimButton(QPushButton, _Hoverable):
    """Кнопка с плавной подсветкой и векторной иконкой. variant: primary | danger | normal | ghost."""

    def __init__(self, text: str = "", icon_kind: str = "", variant: str = "normal", parent=None):
        super().__init__(text, parent)
        self._init_hover()
        self.icon_kind = icon_kind
        self.variant = variant
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setAttribute(Qt.WA_Hover, True)

    def set_variant(self, v: str):
        self.variant = v
        self.update()

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self.text()) + 28 + (26 if self.icon_kind and self.text() else 0)
        if self.icon_kind and not self.text():
            w = 38
        return QSize(w, 36)

    def enterEvent(self, e):
        self._animate_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate_to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _):
        c = palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        enabled = self.isEnabled()
        if self.variant == "primary":
            base, hov, fg, border = QColor(c["accent"]), QColor(c["accent_hover"]), QColor("white"), None
        elif self.variant == "danger":
            base, hov, fg, border = QColor(c["danger"]), QColor(c["danger"]).lighter(115), QColor("white"), None
        elif self.variant == "ghost":
            base, hov, fg, border = QColor(0, 0, 0, 0), QColor(c["hover"]), QColor(c["text"]), None
        else:
            base, hov, fg, border = QColor(c["surface2"]), QColor(c["hover"]), QColor(c["text"]), QColor(c["border"])
        if not enabled:
            base, hov, fg = QColor(c["surface"]), QColor(c["surface"]), QColor(c["muted"])
            border = QColor(c["border"]) if self.variant != "ghost" else None
        bg = lerp_color(base, hov, self._hover)
        if self.isDown() and enabled:
            bg = bg.darker(108)
        p.setBrush(bg)
        if border is not None:
            bc = lerp_color(border, QColor(c["accent"]), self._hover if enabled else 0)
            p.setPen(QPen(bc, 1))
        else:
            p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, 8, 8)
        txt = self.text()
        fm = QFontMetrics(self.font())
        icon_w = 18 if self.icon_kind else 0
        gap = 8 if (txt and icon_w) else 0
        total = icon_w + gap + fm.horizontalAdvance(txt)
        x = (self.width() - total) / 2
        if self.icon_kind:
            draw_glyph(p, self.icon_kind, QRectF(x, (self.height() - icon_w) / 2, icon_w, icon_w), fg)
            x += icon_w + gap
        if txt:
            f = QFont(self.font())
            f.setWeight(QFont.DemiBold if self.variant in ("primary", "danger") else QFont.Normal)
            p.setFont(f)
            p.setPen(fg)
            p.drawText(QRectF(x, 0, fm.horizontalAdvance(txt) + 6, self.height()), Qt.AlignVCenter | Qt.AlignLeft, txt)


class SidebarButton(QAbstractButton, _Hoverable):
    """Пункт боковой панели: иконка, подпись, анимированный индикатор выбранного раздела."""

    def __init__(self, key: str, text: str, icon_kind: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.icon_kind = icon_kind
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(40)
        self._init_hover(160)
        self._sel = 0.0
        self._sanim = QVariantAnimation(self)
        self._sanim.setDuration(220)
        self._sanim.setEasingCurve(QEasingCurve.OutCubic)
        self._sanim.valueChanged.connect(self._on_sel)
        self.toggled.connect(self._toggled)
        self._compact = False

    def set_compact(self, compact: bool):
        self._compact = compact
        self.update()

    def _on_sel(self, v):
        self._sel = float(v)
        self.update()

    def _toggled(self, on: bool):
        self._sanim.stop()
        self._sanim.setStartValue(self._sel)
        self._sanim.setEndValue(1.0 if on else 0.0)
        self._sanim.start()

    def enterEvent(self, e):
        self._animate_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate_to(0.0)
        super().leaveEvent(e)

    def sizeHint(self) -> QSize:
        return QSize(190, 40)

    def paintEvent(self, _):
        c = palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 2, -8, -2)
        bg = QColor(c["hover"])
        bg.setAlphaF(0.9 * max(self._hover * 0.8, self._sel))
        p.setPen(Qt.NoPen)
        p.setBrush(bg if self._sel < 0.99 else QColor(c["select"]))
        p.drawRoundedRect(r, 9, 9)
        if self._sel > 0.01:
            p.setBrush(QColor(c["accent"]))
            h = (r.height() - 14) * self._sel
            p.drawRoundedRect(QRectF(r.left() - 5, r.center().y() - h / 2, 3.5, h), 1.7, 1.7)
        fg = lerp_color(QColor(c["muted"]), QColor(c["text"]), max(self._hover, self._sel))
        icon_col = lerp_color(fg, QColor(c["accent"]), self._sel)
        draw_glyph(p, self.icon_kind, QRectF(r.left() + 12, r.center().y() - 10, 20, 20), icon_col)
        if not self._compact:
            f = QFont(self.font())
            f.setWeight(QFont.DemiBold if self.isChecked() else QFont.Normal)
            p.setFont(f)
            p.setPen(fg)
            p.drawText(QRectF(r.left() + 44, r.top(), r.width() - 50, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       self.text())


class SmoothProgress(QWidget):
    """Полоса прогресса со сглаженной анимацией и неопределённым режимом."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self._value = 0.0
        self._target: Optional[float] = 0.0
        self._phase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(350)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_value)
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self.active = False

    def _set_value(self, v):
        self._value = float(v)
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.02) % 1.0
        self.update()

    def set_fraction(self, f: Optional[float]):
        """f=None — неопределённый прогресс."""
        if f is None:
            self._target = None
            if not self._timer.isActive():
                self._timer.start()
            return
        self._timer.stop()
        f = max(0.0, min(1.0, f))
        if self._target is None or abs(f - (self._target or 0)) > 1e-4:
            self._anim.stop()
            self._anim.setStartValue(self._value)
            self._anim.setEndValue(f)
            self._anim.start()
        self._target = f

    def reset(self):
        self._timer.stop()
        self._anim.stop()
        self._value, self._target = 0.0, 0.0
        self.update()

    def paintEvent(self, _):
        c = palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["surface3"]))
        p.drawRoundedRect(r, 4, 4)
        p.setBrush(QColor(c["accent"]))
        if self._target is None:
            w = r.width() * 0.3
            x = (r.width() + w) * self._phase - w
            clip = QPainterPath()
            clip.addRoundedRect(r, 4, 4)
            p.setClipPath(clip)
            p.drawRoundedRect(QRectF(x, 0, w, r.height()), 4, 4)
        elif self._value > 0:
            p.drawRoundedRect(QRectF(0, 0, max(8.0, r.width() * self._value), r.height()), 4, 4)


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self.setText(text)

    def setText(self, text: str):
        self._full = text
        self.setToolTip(text)
        self._elide()

    def _elide(self):
        super().setText(QFontMetrics(self.font()).elidedText(self._full, Qt.ElideMiddle, max(10, self.width() - 4)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")


class StatCard(Card):
    """Карточка с числом (значение анимированно «набегает»)."""
    clicked = Signal()

    def __init__(self, title: str, icon_kind: str, color: str, clickable: bool = False, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._icon = icon_kind
        self._value = 0.0
        self._fmt: Callable[[float], str] = lambda v: f"{int(v)}"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("muted")
        self.number = QLabel("—")
        self.number.setObjectName("bigNumber")
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        lay.addWidget(self.title)
        lay.addWidget(self.number)
        lay.addWidget(self.sub)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_val)
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
        self._clickable = clickable

    def set_value(self, v: float, fmt: Callable[[float], str], sub: str = "", animate: bool = True):
        self._fmt = fmt
        self.sub.setText(sub)
        self._anim.stop()
        if animate and ANIMATE:
            self._anim.setStartValue(self._value)
            self._anim.setEndValue(float(v))
            self._anim.start()
        else:
            self._on_val(v)

    def _on_val(self, v):
        self._value = float(v)
        self.number.setText(self._fmt(self._value))

    def mousePressEvent(self, e):
        if self._clickable and e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        col = QColor(self._color)
        col.setAlpha(60)
        draw_glyph(p, self._icon, QRectF(self.width() - 52, 12, 34, 34), col)


class DonutChart(QWidget):
    """Кольцевая диаграмма с легендой, анимацией появления и подсветкой сегмента под курсором."""
    segmentClicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(190)
        self.setMouseTracking(True)
        self._items: list[tuple[str, float, str, str]] = []   # key, value, color, value_text
        self._label_map: dict[str, str] = {}
        self._prog = 1.0
        self._hover: Optional[int] = None
        self._center_title = ""
        self._center_sub = ""
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(900)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v):
        self._prog = float(v)
        self.update()

    def set_data(self, items: list[tuple[str, str, float, str, str]], center_title: str, center_sub: str):
        """items: (key, подпись, значение, цвет, текст значения)."""
        self._items = [(k, v, col, vt) for k, _, v, col, vt in items]
        self._label_map = {k: lab for k, lab, *_ in items}
        self._center_title, self._center_sub = center_title, center_sub
        self._anim.stop()
        if not ANIMATE:
            self._on_anim(1.0)
            return
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def _geometry(self):
        s = min(self.height() - 10, self.width() * 0.45, 230)
        return QRectF(8, (self.height() - s) / 2, s, s)

    def _angle_at(self, pos: QPointF) -> Optional[int]:
        rect = self._geometry()
        d = pos - rect.center()
        dist = math.hypot(d.x(), d.y())
        if not (rect.width() * 0.3 <= dist <= rect.width() * 0.52) or not self._items:
            return None
        ang = (math.degrees(math.atan2(-d.y(), d.x())) - 90) % 360
        ang = (360 - ang) % 360
        total = sum(v for _, v, _, _ in self._items) or 1
        acc = 0.0
        for i, (_, v, _, _) in enumerate(self._items):
            acc += v / total * 360
            if ang <= acc:
                return i
        return None

    def mouseMoveEvent(self, e):
        h = self._angle_at(e.position())
        if h != self._hover:
            self._hover = h
            self.setCursor(Qt.PointingHandCursor if h is not None else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        h = self._angle_at(e.position())
        if h is not None:
            self.segmentClicked.emit(self._items[h][0])

    def paintEvent(self, _):
        c = palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self._geometry()
        total = sum(v for _, v, _, _ in self._items)
        thick = rect.width() * 0.17
        if total <= 0:
            pen = QPen(QColor(c["surface3"]), thick)
            p.setPen(pen)
            p.drawEllipse(rect.adjusted(thick / 2, thick / 2, -thick / 2, -thick / 2))
            p.setPen(QColor(c["muted"]))
            p.drawText(rect, Qt.AlignCenter, "Нет данных")
            return
        start = 90.0
        for i, (key, v, col, _) in enumerate(self._items):
            span = -v / total * 360 * self._prog
            t = thick * (1.18 if i == self._hover else 1.0)
            pen = QPen(QColor(col), t)
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            inner = rect.adjusted(thick / 2, thick / 2, -thick / 2, -thick / 2)
            gap = 1.2 if len(self._items) > 1 else 0
            p.drawArc(inner, int((start - (gap / 2 if span < 0 else 0)) * 16), int((span + gap) * 16) if abs(span) > gap else int(span * 16))
            start += span
        if self._hover is not None and self._hover < len(self._items):
            key, v, col, vt = self._items[self._hover]
            title, sub = self._label_map.get(key, key), f"{vt} · {v / total * 100:.0f}%"
        else:
            title, sub = self._center_title, self._center_sub
        p.setPen(QColor(c["text"]))
        f = QFont(self.font())
        f.setPointSizeF(13)
        f.setBold(True)
        p.setFont(f)
        cr = QRectF(rect.center().x() - rect.width() * 0.3, rect.center().y() - 22, rect.width() * 0.6, 26)
        p.drawText(cr, Qt.AlignCenter, QFontMetrics(f).elidedText(title, Qt.ElideRight, int(cr.width())))
        f2 = QFont(self.font())
        f2.setPointSizeF(9)
        p.setFont(f2)
        p.setPen(QColor(c["muted"]))
        cr2 = QRectF(cr.left(), cr.bottom() - 2, cr.width(), 22)
        p.drawText(cr2, Qt.AlignHCenter | Qt.AlignTop, QFontMetrics(f2).elidedText(sub, Qt.ElideRight, int(cr.width())))
        # легенда
        lx = rect.right() + 22
        row_h = min(26, max(18, (self.height() - 16) / max(1, len(self._items))))
        y = (self.height() - row_h * len(self._items)) / 2
        p.setFont(self.font())
        fm = QFontMetrics(self.font())
        for i, (key, v, col, vt) in enumerate(self._items):
            p.setBrush(QColor(col))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(lx, y + row_h / 2 - 5, 10, 10), 3, 3)
            p.setPen(QColor(c["text"] if i == self._hover else c["muted"]))
            lab = self._label_map.get(key, key)
            avail = self.width() - lx - 20
            vw = fm.horizontalAdvance(vt)
            p.drawText(QRectF(lx + 18, y, avail - vw - 20, row_h), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(lab, Qt.ElideRight, int(avail - vw - 24)))
            p.drawText(QRectF(lx + 18, y, avail, row_h), Qt.AlignVCenter | Qt.AlignRight, vt)
            y += row_h


class BarChart(QWidget):
    """Горизонтальные столбики с подписями и анимацией роста."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(190)
        self._items: list[tuple[str, float, str, str]] = []
        self._prog = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(800)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, v):
        self._prog = float(v)
        self.update()

    def set_data(self, items: list[tuple[str, float, str, str]]):
        """items: (подпись, значение, цвет, текст значения)."""
        self._items = items
        self._anim.stop()
        if not ANIMATE:
            self._on_anim(1.0)
            return
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def paintEvent(self, _):
        c = palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self._items:
            p.setPen(QColor(c["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Нет данных")
            return
        n = len(self._items)
        row_h = min(28, (self.height() - 4) / n)
        label_w = 86
        val_w = 92
        mx = max(v for _, v, _, _ in self._items) or 1
        fm = QFontMetrics(self.font())
        y = 2
        for lab, v, col, vt in self._items:
            p.setPen(QColor(c["text"]))
            p.drawText(QRectF(0, y, label_w - 8, row_h), Qt.AlignVCenter | Qt.AlignRight,
                       fm.elidedText(lab, Qt.ElideRight, label_w - 10))
            track = QRectF(label_w, y + row_h * 0.25, self.width() - label_w - val_w, row_h * 0.5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["surface3"]))
            p.drawRoundedRect(track, track.height() / 2, track.height() / 2)
            w = max(track.height(), track.width() * (v / mx) * self._prog)
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(track.left(), track.top(), w, track.height()), track.height() / 2, track.height() / 2)
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(self.width() - val_w + 8, y, val_w - 8, row_h), Qt.AlignVCenter | Qt.AlignLeft, vt)
            y += row_h


class UsageBar(QWidget):
    """Заполнение диска: занято/всего."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(8)
        self.fraction = 0.0

    def paintEvent(self, _):
        c = palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["surface3"]))
        p.drawRoundedRect(r, 4, 4)
        col = QColor(c["danger"] if self.fraction > 0.9 else c["warn"] if self.fraction > 0.75 else c["accent"])
        p.setBrush(col)
        p.drawRoundedRect(QRectF(0, 0, max(6.0, r.width() * self.fraction), r.height()), 4, 4)


class TaskThread(QThread):
    """Выполняет функцию в фоне. Сигналы: progress(текст, сделано, всего), done(результат), failed(сообщение)."""
    progress = Signal(str, int, int)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.stop_event = threading.Event()

    def run(self):
        try:
            result = self._fn(lambda t, a=0, b=0: self.progress.emit(t, a, b), self.stop_event)
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку фоновой задачи
            import logging
            logging.getLogger("deepsearch").exception("Фоновая задача завершилась ошибкой")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
