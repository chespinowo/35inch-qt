"""Protocolo serial dos displays USB "Turing Smart Screen Rev A" / UsbMonitor.

Todo comando começa com um cabeçalho de 6 bytes que empacota quatro
coordenadas de 10 bits (x, y, ex, ey) e o byte do comando:

    byte0 = x >> 2
    byte1 = ((x & 3) << 6) | (y >> 4)
    byte2 = ((y & 15) << 4) | (ex >> 6)
    byte3 = ((ex & 63) << 2) | (ey >> 8)
    byte4 = ey & 255
    byte5 = comando

Imagens seguem o cabeçalho DISPLAY_BITMAP como pixels RGB565 little-endian,
em ordem de linha, cobrindo o retângulo (x, y)-(ex, ey) com extremos inclusivos.

Este módulo é puro (sem Qt, sem serial) para poder ser testado isoladamente.
"""

from __future__ import annotations

from enum import IntEnum

USB_VID = 0x1A86
USB_PID = 0x5722
SERIAL_NUMBER_HINT = "USB35INCHIPSV2"
BAUDRATE = 115200

PANEL_WIDTH = 320
PANEL_HEIGHT = 480

MAX_COORD = 1023  # cada coordenada ocupa 10 bits


class Command(IntEnum):
    HELLO = 69
    RESET = 101
    CLEAR = 102  # limpa para branco
    TO_BLACK = 103
    SCREEN_OFF = 108
    SCREEN_ON = 109
    SET_BRIGHTNESS = 110
    SET_ORIENTATION = 121
    DISPLAY_BITMAP = 197


class Orientation(IntEnum):
    PORTRAIT = 0
    REVERSE_PORTRAIT = 1
    LANDSCAPE = 2
    REVERSE_LANDSCAPE = 3

    @property
    def is_landscape(self) -> bool:
        return self in (Orientation.LANDSCAPE, Orientation.REVERSE_LANDSCAPE)


# Resposta de 6 bytes ao HELLO -> (modelo, largura, altura em retrato).
# Telas Turing 3.5" oficiais não respondem ao HELLO.
HELLO_MODELS: dict[bytes, tuple[str, int, int]] = {
    bytes([1] * 6): ('UsbMonitor 3.5"', 320, 480),
    bytes([2] * 6): ('UsbMonitor 5"', 480, 800),
    bytes([3] * 6): ('UsbMonitor 7"', 600, 1024),
}
UNKNOWN_MODEL = ('Turing 3.5" (sem resposta ao HELLO)', PANEL_WIDTH, PANEL_HEIGHT)


def encode_command(cmd: int, x: int = 0, y: int = 0, ex: int = 0, ey: int = 0) -> bytes:
    """Monta o cabeçalho de 6 bytes."""
    for name, value in (("x", x), ("y", y), ("ex", ex), ("ey", ey)):
        if not 0 <= value <= MAX_COORD:
            raise ValueError(f"{name}={value} fora do intervalo 0..{MAX_COORD}")
    return bytes(
        [
            x >> 2,
            ((x & 3) << 6) | (y >> 4),
            ((y & 15) << 4) | (ex >> 6),
            ((ex & 63) << 2) | (ey >> 8),
            ey & 255,
            int(cmd),
        ]
    )


def decode_command(header: bytes) -> tuple[int, int, int, int, int]:
    """Inverso de encode_command: devolve (cmd, x, y, ex, ey)."""
    if len(header) < 6:
        raise ValueError("cabeçalho precisa de 6 bytes")
    b0, b1, b2, b3, b4, cmd = header[:6]
    x = (b0 << 2) | (b1 >> 6)
    y = ((b1 & 0x3F) << 4) | (b2 >> 4)
    ex = ((b2 & 0x0F) << 6) | (b3 >> 2)
    ey = ((b3 & 0x03) << 8) | b4
    return cmd, x, y, ex, ey


def hello_packet() -> bytes:
    return bytes([Command.HELLO]) * 6


def level_to_raw_brightness(level: int, invert: bool = False) -> int:
    """Converte 0..100 % para o byte do display.

    O firmware conhecido usa 0 = mais claro e 255 = mais escuro, então o padrão
    inverte o percentual. `invert=True` usa a escala direta, caso a sua
    unidade se comporte ao contrário.
    """
    level = max(0, min(100, int(level)))
    if invert:
        return int(level / 100 * 255)
    return int(255 - level / 100 * 255)


def orientation_packet(orientation: int, width: int, height: int) -> bytes:
    """Pacote de 16 bytes de SET_ORIENTATION.

    `width`/`height` são as dimensões já na nova orientação.
    """
    buf = bytearray(16)
    buf[0:6] = encode_command(Command.SET_ORIENTATION)
    buf[6] = int(orientation) + 100
    buf[7:9] = width.to_bytes(2, "big")
    buf[9:11] = height.to_bytes(2, "big")
    return bytes(buf)


def oriented_size(orientation: int, panel_w: int = PANEL_WIDTH, panel_h: int = PANEL_HEIGHT) -> tuple[int, int]:
    """Tamanho (largura, altura) do quadro para a orientação dada."""
    if Orientation(orientation).is_landscape:
        return panel_h, panel_w
    return panel_w, panel_h
