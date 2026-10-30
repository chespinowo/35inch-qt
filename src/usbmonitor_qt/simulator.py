"""Modelo de software do display: interpreta o fluxo de bytes do protocolo.

Serve ao modo simulador (sem hardware) e aos testes, que verificam que o que
sai pela porta serial reconstrói exatamente a imagem esperada.
"""

from __future__ import annotations

from PySide6.QtGui import QImage

from .imaging import rgb565_to_qimage
from .protocol import (
    PANEL_HEIGHT,
    PANEL_WIDTH,
    Command,
    Orientation,
    decode_command,
    oriented_size,
)


class ScreenModel:
    def __init__(self, panel_w: int = PANEL_WIDTH, panel_h: int = PANEL_HEIGHT):
        self.panel_w, self.panel_h = panel_w, panel_h
        self.orientation = Orientation.PORTRAIT
        self.width, self.height = panel_w, panel_h
        self.framebuffer = bytearray(self.width * self.height * 2)
        self.screen_on = True
        self.brightness_raw: int | None = None
        self.commands: list[int] = []
        self.errors: list[str] = []
        self.reset_requested = False
        self._buf = bytearray()
        self._bitmap: tuple[int, int, int, int] | None = None
        self._pixels = bytearray()

    # -- entrada ---------------------------------------------------------
    def feed(self, data: bytes) -> None:
        self._buf.extend(data)
        while True:
            if self._bitmap is not None:
                x0, y0, x1, y1 = self._bitmap
                total = (x1 - x0 + 1) * (y1 - y0 + 1) * 2
                take = bytes(self._buf[: total - len(self._pixels)])
                del self._buf[: len(take)]
                self._pixels.extend(take)
                if len(self._pixels) < total:
                    return
                self._paint(x0, y0, x1, y1, bytes(self._pixels))
                self._bitmap, self._pixels = None, bytearray()
                continue
            if len(self._buf) < 6:
                return
            cmd, x, y, ex, ey = decode_command(bytes(self._buf[:6]))
            if cmd == Command.SET_ORIENTATION:
                if len(self._buf) < 16:
                    return
                packet = bytes(self._buf[:16])
                del self._buf[:16]
                self._set_orientation(packet)
                continue
            del self._buf[:6]
            self.commands.append(cmd)
            if cmd == Command.DISPLAY_BITMAP:
                if not (0 <= x <= ex < self.width and 0 <= y <= ey < self.height):
                    self.errors.append(f"retângulo fora da tela: ({x},{y})-({ex},{ey}) em {self.width}x{self.height}")
                    continue
                self._bitmap = (x, y, ex, ey)
            elif cmd == Command.CLEAR:
                self.framebuffer = bytearray(b"\xff" * len(self.framebuffer))
            elif cmd == Command.SCREEN_ON:
                self.screen_on = True
            elif cmd == Command.SCREEN_OFF:
                self.screen_on = False
            elif cmd == Command.SET_BRIGHTNESS:
                self.brightness_raw = x
            elif cmd == Command.RESET:
                self.reset_requested = True
            elif cmd == Command.HELLO:
                pass
            else:
                self.errors.append(f"comando desconhecido: {cmd}")

    # -- saída -----------------------------------------------------------
    def to_qimage(self) -> QImage:
        return rgb565_to_qimage(self.framebuffer, self.width, self.height)

    # -- internos --------------------------------------------------------
    def _set_orientation(self, packet: bytes) -> None:
        self.commands.append(Command.SET_ORIENTATION)
        try:
            self.orientation = Orientation(packet[6] - 100)
        except ValueError:
            self.errors.append(f"orientação inválida: {packet[6]}")
            return
        self.width = int.from_bytes(packet[7:9], "big")
        self.height = int.from_bytes(packet[9:11], "big")
        expected = oriented_size(self.orientation, self.panel_w, self.panel_h)
        if (self.width, self.height) != expected:
            self.errors.append(f"tamanho {self.width}x{self.height} não bate com a orientação {self.orientation.name}")
        self.framebuffer = bytearray(self.width * self.height * 2)

    def _paint(self, x0: int, y0: int, x1: int, y1: int, pixels: bytes) -> None:
        row_len = (x1 - x0 + 1) * 2
        for r, y in enumerate(range(y0, y1 + 1)):
            start = (y * self.width + x0) * 2
            self.framebuffer[start : start + row_len] = pixels[r * row_len : (r + 1) * row_len]
