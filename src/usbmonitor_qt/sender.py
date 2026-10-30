"""Envio de quadros com atualização parcial (só a região que mudou)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QImage

from .device import BaseDisplay
from .imaging import to_rgb565_rows

Rect = tuple[int, int, int, int]  # x0, y0, x1, y1 (inclusivo)


class SizeMismatch(ValueError):
    """O quadro não tem o tamanho atual do display (ex.: durante troca de orientação)."""


@dataclass
class SendReport:
    rects: list[Rect]  # vazio = nada mudou, nada foi enviado
    bytes_sent: int
    full: bool


def diff_rects(old: list[bytes] | None, new: list[bytes], merge_gap: int = 4) -> list[Rect]:
    """Regiões (x0, y0, x1, y1 inclusivos) que contêm todos os pixels diferentes.

    Linhas alteradas próximas (a menos de `merge_gap` linhas de distância) formam uma
    única faixa; cada faixa vira um retângulo com apenas as colunas que mudaram nela.
    Isso evita reenviar áreas estáticas que ficam entre duas regiões que mudam.
    Sem quadro anterior (ou com tamanho diferente), devolve a tela inteira.
    """
    height, width = len(new), len(new[0]) // 2
    if old is None or len(old) != height or len(old[0]) != len(new[0]):
        return [(0, 0, width - 1, height - 1)]
    bands: list[list[int]] = []  # [topo, base, esquerda, direita]
    for y, (a, b) in enumerate(zip(old, new)):
        if a == b:
            continue
        x = int.from_bytes(a, "little") ^ int.from_bytes(b, "little")
        lo = ((x & -x).bit_length() - 1) >> 4  # bit -> byte -> pixel (2 bytes)
        hi = (x.bit_length() - 1) >> 4
        if bands and y - bands[-1][1] <= merge_gap:
            band = bands[-1]
            band[1] = y
            band[2], band[3] = min(band[2], lo), max(band[3], hi)
        else:
            bands.append([y, y, lo, hi])
    return [(left, top, right, bottom) for top, bottom, left, right in bands]


class FrameSender:
    #: acima dessa fração da tela, vale mais enviar o quadro inteiro
    FULL_THRESHOLD = 0.75

    def __init__(self, display: BaseDisplay, partial: bool = True) -> None:
        self.display = display
        self.partial = partial
        self._rows: list[bytes] | None = None

    def invalidate(self) -> None:
        """Esquece o último quadro: o próximo envio será da tela inteira."""
        self._rows = None

    def send(self, image: QImage) -> SendReport:
        d = self.display
        if (image.width(), image.height()) != (d.width, d.height):
            raise SizeMismatch(f"quadro {image.width()}x{image.height()} != display {d.width}x{d.height}")
        rows = to_rgb565_rows(image)
        rects = diff_rects(self._rows if self.partial else None, rows)
        if not rects:
            return SendReport([], 0, False)
        area = sum((x1 - x0 + 1) * (y1 - y0 + 1) for x0, y0, x1, y1 in rects)
        full = area >= self.FULL_THRESHOLD * d.width * d.height
        if full:
            rects = [(0, 0, d.width - 1, d.height - 1)]
        before = d.bytes_written
        for x0, y0, x1, y1 in rects:
            data = b"".join(row[x0 * 2 : (x1 + 1) * 2] for row in rows[y0 : y1 + 1])
            d.blit(x0, y0, x1, y1, data)
        self._rows = rows  # só depois de enviar com sucesso
        return SendReport(rects, d.bytes_written - before, full)
