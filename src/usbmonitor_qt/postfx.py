"""Efeitos aplicados ao quadro final, depois que a cena foi desenhada."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter


def _flip(image: QImage, horizontal: bool, vertical: bool) -> QImage:
    if hasattr(image, "flipped"):  # Qt >= 6.9; `mirrored` foi depreciado
        axes = Qt.Orientation(0)
        if horizontal:
            axes |= Qt.Orientation.Horizontal
        if vertical:
            axes |= Qt.Orientation.Vertical
        return image.flipped(axes)
    return image.mirrored(horizontal, vertical)


def warm_tint(strength: int) -> QColor:
    """Cor multiplicadora da "luz noturna": 0 = branco (sem efeito), 100 = âmbar forte."""
    s = max(0, min(100, int(strength))) / 100
    return QColor(255, round(255 - 76 * s), round(255 - 166 * s))


def apply_effects(image: QImage, warmth: int = 0, mirror_h: bool = False, mirror_v: bool = False) -> QImage:
    """Espelhamento e tom quente. Sem nenhum efeito ativo devolve a própria imagem, sem copiar."""
    if mirror_h or mirror_v:
        image = _flip(image, mirror_h, mirror_v)
    if warmth > 0:
        if image.format() != QImage.Format.Format_RGB32:
            image = image.convertToFormat(QImage.Format.Format_RGB32)
        else:
            image = image.copy()
        painter = QPainter(image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        painter.fillRect(image.rect(), warm_tint(warmth))
        painter.end()
    return image
