"""Tema escuro da aplicação e ícone gerado por código."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap

ACCENT = "#38bdf8"
CHECK_ICON = (Path(__file__).parent / "assets" / "check.svg").as_posix()

_QSS = """
* { font-size: 13px; }
QWidget { background: transparent; color: #e6edf3; }
QMainWindow, QDialog, QMessageBox, QColorDialog, QFileDialog { background: #0e1116; }
QToolTip { background: #1b212b; color: #e6edf3; border: 1px solid #2b3544; padding: 4px 6px; }

QFrame#card { background: #151a22; border: 1px solid #232b37; border-radius: 14px; }
QLabel#h1 { font-size: 15px; font-weight: 600; }
QLabel#muted { color: #8b98a9; }
QLabel#hint { color: #fbbf24; background: #2a2412; border: 1px solid #4a3d16; border-radius: 8px; padding: 9px 11px; }

QTabWidget::pane { border: 0; top: 6px; }
QTabBar::tab { background: transparent; color: #8b98a9; padding: 8px 16px; margin-right: 4px; border-radius: 9px; font-weight: 600; }
QTabBar::tab:selected { background: #1b2430; color: #e6edf3; }
QTabBar::tab:hover:!selected { color: #e6edf3; }

QPushButton { background: #1c2330; border: 1px solid #2b3544; border-radius: 9px; padding: 7px 14px; }
QPushButton:hover { background: #232c3b; border-color: #3a475a; }
QPushButton:pressed { background: #1a212c; }
QPushButton:disabled { color: #5b6676; background: #141922; border-color: #1f2733; }
QPushButton[primary="true"] { background: @ACCENT@; border-color: @ACCENT@; color: #06121a; font-weight: 700; }
QPushButton[primary="true"]:hover { background: @ACCENT_HOVER@; border-color: @ACCENT_HOVER@; }
QPushButton[primary="true"]:disabled { background: #1e4a5e; border-color: #1e4a5e; color: #7fa6b8; }
QPushButton[seg="true"]:checked { background: @ACCENT_DIM@; border-color: @ACCENT@; font-weight: 600; }
QPushButton[danger="true"] { color: #fca5a5; }

QComboBox, QLineEdit, QSpinBox, QTimeEdit, QPlainTextEdit {
    background: #10151c; border: 1px solid #2b3544; border-radius: 8px; padding: 6px 9px;
    selection-background-color: @ACCENT_DIM@;
}
QComboBox:hover, QLineEdit:focus, QSpinBox:focus, QTimeEdit:focus, QPlainTextEdit:focus { border-color: @ACCENT@; }
QComboBox QAbstractItemView { background: #151a22; border: 1px solid #2b3544; selection-background-color: @ACCENT_DIM@; outline: 0; }
QComboBox QAbstractItemView::item:disabled { color: #5b6676; }

QMenu { background: #151a22; border: 1px solid #2b3544; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 7px 24px 7px 14px; border-radius: 6px; }
QMenu::item:selected { background: @ACCENT_DIM@; }
QMenu::item:disabled { color: #5b6676; }
QMenu::separator { height: 1px; background: #2b3544; margin: 5px 8px; }
QMenu::indicator { width: 14px; height: 14px; margin-left: 4px; }

QScrollArea { border: 0; background: transparent; }

QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid #3a475a; background: #10151c; }
QCheckBox::indicator:hover { border-color: @ACCENT@; }
QCheckBox::indicator:checked { background: @ACCENT@; border-color: @ACCENT@; image: url(@CHECK@); }

QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: #232c3b; }
QSlider::sub-page:horizontal { background: @ACCENT@; border-radius: 3px; }
QSlider::handle:horizontal { background: #f1f5f9; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #2b3544; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() * (1 - t) + b.red() * t),
        round(a.green() * (1 - t) + b.green() * t),
        round(a.blue() * (1 - t) + b.blue() * t),
    )


def stylesheet(accent: str = ACCENT) -> str:
    base = QColor(accent)
    return (
        _QSS.replace("@ACCENT_HOVER@", _mix(base, QColor("#ffffff"), 0.18).name())
        .replace("@ACCENT_DIM@", _mix(base, QColor("#151a22"), 0.78).name())
        .replace("@ACCENT@", base.name())
        .replace("@CHECK@", CHECK_ICON)
    )


STATUS_COLORS = {"ok": "#34d399", "wait": "#fbbf24", "off": "#6b7686", "err": "#f87171"}


def app_icon(status: str | None = None) -> QIcon:
    """Ícone do aplicativo; com `status` ("ok", "wait", "off", "err") ganha um ponto colorido no canto."""
    icon = QIcon()
    for size in (32, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size
        body = QRectF(s * 0.2, s * 0.06, s * 0.6, s * 0.88)
        p.setPen(QPen(QColor("#2a3340"), max(1.0, s * 0.03)))
        p.setBrush(QColor("#0b0e13"))
        p.drawRoundedRect(body, s * 0.1, s * 0.1)
        screen = body.adjusted(s * 0.05, s * 0.05, -s * 0.05, -s * 0.05)
        grad = QLinearGradient(screen.topLeft(), screen.bottomRight())
        grad.setColorAt(0, QColor(ACCENT))
        grad.setColorAt(1, QColor("#6366f1"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(screen, s * 0.05, s * 0.05)
        if status in STATUS_COLORS:
            r = s * 0.17
            c = QRectF(s - r * 2 - s * 0.02, s - r * 2 - s * 0.02, r * 2, r * 2)
            p.setPen(QPen(QColor("#0b0e13"), max(1.5, s * 0.05)))
            p.setBrush(QColor(STATUS_COLORS[status]))
            p.drawEllipse(c)
        p.end()
        icon.addPixmap(pm)
    return icon
