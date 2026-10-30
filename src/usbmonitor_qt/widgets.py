"""Widgets pequenos reutilizados pela interface."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget


class ColorButton(QPushButton):
    colorChanged = Signal(QColor)

    def __init__(self, color: str = "#38bdf8", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(64, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._color = QColor(color)
        self.clicked.connect(self._pick)
        self._paint()

    def color(self) -> QColor:
        return QColor(self._color)

    def setColor(self, color: QColor | str, emit: bool = False) -> None:
        self._color = QColor(color)
        self._paint()
        if emit:
            self.colorChanged.emit(self._color)

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(self._color, self.window(), "Escolher cor")
        if chosen.isValid():
            self.setColor(chosen, emit=True)

    def _paint(self) -> None:
        self.setStyleSheet(
            f"QPushButton {{ background: {self._color.name()}; border: 2px solid #3a4556; border-radius: 8px; }}"
            "QPushButton:hover { border-color: #9fb3cc; }"
        )


class SliderRow(QWidget):
    valueChanged = Signal(int)

    def __init__(self, lo: int, hi: int, value: int, suffix: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._suffix = suffix
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(value)
        self.label = QLabel()
        self.label.setObjectName("muted")
        self.label.setMinimumWidth(52)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(self.slider, 1)
        row.addWidget(self.label)
        self.slider.valueChanged.connect(self._on_change)
        self._refresh(value)

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, v: int) -> None:
        self.slider.setValue(v)

    def _on_change(self, v: int) -> None:
        self._refresh(v)
        self.valueChanged.emit(v)

    def _refresh(self, v: int) -> None:
        self.label.setText(f"{v}{self._suffix}")


class Card(QFrame):
    """Painel arredondado com título opcional."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(18, 16, 18, 18)
        self._layout.setSpacing(12)
        if title:
            label = QLabel(title)
            label.setObjectName("h1")
            self._layout.addWidget(label)

    def add(self, widget: QWidget | QHBoxLayout, stretch: int = 0) -> None:
        if isinstance(widget, QWidget):
            self._layout.addWidget(widget, stretch)
        else:
            self._layout.addLayout(widget, stretch)
