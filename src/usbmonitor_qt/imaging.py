"""Conversão de QImage para o formato de pixel do display (RGB565 little-endian)."""

from __future__ import annotations

import sys
from array import array

from PySide6.QtGui import QImage


def to_rgb565_rows(image: QImage) -> list[bytes]:
    """Devolve uma lista com uma `bytes` por linha, 2 bytes por pixel (RGB565 LE)."""
    img = image.convertToFormat(QImage.Format.Format_RGB16)
    width, height, stride = img.width(), img.height(), img.bytesPerLine()
    raw = bytes(img.constBits())
    row_len = width * 2
    rows = [raw[y * stride : y * stride + row_len] for y in range(height)]
    if sys.byteorder == "big":  # Format_RGB16 usa a ordem nativa da máquina
        swapped = []
        for row in rows:
            a = array("H", row)
            a.byteswap()
            swapped.append(a.tobytes())
        rows = swapped
    return rows


def rgb565_to_qimage(data: bytes | bytearray, width: int, height: int) -> QImage:
    """Inverso de to_rgb565_rows, usado pelo simulador e pelos testes."""
    buf = bytes(data)  # QImage não copia o buffer: ele precisa viver até o .copy()
    if sys.byteorder == "big":
        a = array("H", buf)
        a.byteswap()
        buf = a.tobytes()
    img = QImage(buf, width, height, width * 2, QImage.Format.Format_RGB16)
    result = img.copy()
    del img
    return result
