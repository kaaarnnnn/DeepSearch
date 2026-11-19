"""Цветовые схемы и таблица стилей Qt (тёмная тема по умолчанию)."""
from __future__ import annotations

DARK = {
    "bg": "#0E1116", "surface": "#151A22", "surface2": "#1B212B", "surface3": "#232B38",
    "border": "#2A3240", "text": "#E7EAF0", "muted": "#8D97A8", "accent": "#4C8DFF", "accent2": "#7C5CFF",
    "accent_hover": "#6AA1FF", "danger": "#FF5C6C", "ok": "#33D69F", "warn": "#FFB020",
    "select": "#25406E", "hover": "#1F2631", "input": "#10141B",
}
LIGHT = {
    "bg": "#F3F5F9", "surface": "#FFFFFF", "surface2": "#F0F2F7", "surface3": "#E4E8F0",
    "border": "#D5DAE4", "text": "#1B2230", "muted": "#68738A", "accent": "#2F6FE4", "accent2": "#6B4CE0",
    "accent_hover": "#4A86F0", "danger": "#E5384B", "ok": "#12A878", "warn": "#E39400",
    "select": "#CFE0FF", "hover": "#E9EDF5", "input": "#FFFFFF",
}

_current = dict(DARK)


def palette(name: str | None = None) -> dict:
    if name:
        _current.clear()
        _current.update(LIGHT if name == "light" else DARK)
    return _current


def qss(c: dict, check_icon: str = "") -> str:
    check = f"image: url({check_icon});" if check_icon else ""
    return f"""
* {{ font-family: "Segoe UI Variable", "Segoe UI", "Inter", "Noto Sans", sans-serif; font-size: 13px; }}
QWidget {{ color: {c['text']}; }}
QMainWindow, QDialog, #root {{ background: {c['bg']}; }}
#content {{ background: {c['bg']}; }}
#card, QFrame#card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: 24px; font-weight: 600; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; }}
QLabel#muted, QLabel[muted="true"] {{ color: {c['muted']}; }}
QLabel#bigNumber {{ font-size: 26px; font-weight: 700; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {c['input']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 7px 10px;
    selection-background-color: {c['accent']}; selection-color: white; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {c['accent']}; }}
QLineEdit#search {{ font-size: 14px; padding: 9px 12px 9px 36px; border-radius: 10px; background: {c['surface']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c['surface2']}; border: 1px solid {c['border']}; outline: 0;
    selection-background-color: {c['select']}; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; }}

QPushButton {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ background: {c['hover']}; border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['surface']}; border-color: {c['border']}; }}
QPushButton[variant="primary"] {{ background: {c['accent']}; border: none; color: white; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {c['accent_hover']}; }}
QPushButton[variant="danger"] {{ background: {c['danger']}; border: none; color: white; font-weight: 600; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {c['hover']}; border-color: {c['border']}; }}
QPushButton[chip="true"] {{ border-radius: 14px; padding: 4px 12px; background: {c['surface']}; }}
QPushButton[chip="true"]:checked {{ background: {c['select']}; border-color: {c['accent']}; color: {c['text']}; }}

QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {c['border']}; background: {c['input']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; {check} }}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}

QTableView {{ background: {c['surface']}; alternate-background-color: {c['surface2']}; border: 1px solid {c['border']};
    border-radius: 10px; gridline-color: transparent; selection-background-color: {c['select']};
    selection-color: {c['text']}; outline: 0; }}
QTableView::item {{ padding: 2px 6px; border: none; }}
QTableView::item:hover {{ background: {c['hover']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {c['surface']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 8px 8px; font-weight: 600; }}
QHeaderView::section:hover {{ color: {c['text']}; background: {c['hover']}; }}
QTableCornerButton::section {{ background: {c['surface']}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {c['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QMenu {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 26px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['select']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 5px 8px; }}
QToolTip {{ background: {c['surface3']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px; }}
QListWidget {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 4px; outline: 0; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {c['select']}; color: {c['text']}; }}
QGroupBox {{ border: none; margin-top: 6px; font-weight: 600; }}
QStatusBar {{ background: {c['surface']}; }}
QSplitter::handle {{ background: transparent; }}
QFrame#drivePopup {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#statusbar {{ background: {c['surface']}; border-top: 1px solid {c['border']}; }}
QFrame#sidebar {{ background: {c['surface']}; border-right: 1px solid {c['border']}; }}
QFrame#banner {{ background: {c['surface2']}; border: 1px solid {c['warn']}; border-radius: 10px; }}
"""
