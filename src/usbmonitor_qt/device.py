"""Drivers do display: serial real (UsbMonitor 1a86:5722) e simulador."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .protocol import (
    BAUDRATE,
    HELLO_MODELS,
    PANEL_HEIGHT,
    PANEL_WIDTH,
    SERIAL_NUMBER_HINT,
    UNKNOWN_MODEL,
    USB_PID,
    USB_VID,
    Command,
    Orientation,
    encode_command,
    hello_packet,
    level_to_raw_brightness,
    orientation_packet,
    oriented_size,
)
from .simulator import ScreenModel

PORT_AUTO = "AUTO"
PORT_SIMULATOR = "SIM"

PERMISSION_HINT = (
    "Sem permissão para abrir {port}. Instale a regra udev (udev/60-usbmonitor.rules) "
    "e replugue o display, ou adicione seu usuário ao grupo 'dialout'."
)


class DisplayError(RuntimeError):
    """Falha de comunicação com o display (porta ausente, desconectada, sem permissão...)."""


@dataclass
class PortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None
    serial_number: str | None

    @property
    def is_target(self) -> bool:
        return (self.vid == USB_VID and self.pid == USB_PID) or self.serial_number == SERIAL_NUMBER_HINT


def list_ports() -> list[PortInfo]:
    try:
        from serial.tools.list_ports import comports
    except ImportError:  # pragma: no cover
        return []
    ports = [PortInfo(p.device, p.description or "", p.vid, p.pid, p.serial_number) for p in comports()]
    return sorted(ports, key=lambda p: (not p.is_target, p.device))


def find_target_port() -> str | None:
    for port in list_ports():
        if port.is_target:
            return port.device
    return None


class BaseDisplay:
    """Comandos de alto nível sobre um canal de bytes (`_write`)."""

    def __init__(self) -> None:
        self.orientation = Orientation.PORTRAIT
        self.panel_w, self.panel_h = PANEL_WIDTH, PANEL_HEIGHT
        self.model = "Desconhecido"
        self.port = ""
        self.invert_brightness = False
        self.bytes_written = 0

    # -- a implementar ---------------------------------------------------
    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _write(self, data: bytes) -> None:
        raise NotImplementedError

    # -- geometria -------------------------------------------------------
    @property
    def width(self) -> int:
        return oriented_size(self.orientation, self.panel_w, self.panel_h)[0]

    @property
    def height(self) -> int:
        return oriented_size(self.orientation, self.panel_w, self.panel_h)[1]

    # -- comandos --------------------------------------------------------
    def screen_on(self) -> None:
        self._write(encode_command(Command.SCREEN_ON))

    def screen_off(self) -> None:
        self._write(encode_command(Command.SCREEN_OFF))

    def set_brightness(self, level: int) -> None:
        raw = level_to_raw_brightness(level, self.invert_brightness)
        self._write(encode_command(Command.SET_BRIGHTNESS, raw))

    def set_orientation(self, orientation: int) -> None:
        self.orientation = Orientation(orientation)
        self._write(orientation_packet(self.orientation, self.width, self.height))

    def clear(self) -> None:
        # O firmware só limpa direito em retrato.
        previous = self.orientation
        self.set_orientation(Orientation.PORTRAIT)
        self._write(encode_command(Command.CLEAR))
        self.set_orientation(previous)

    def blit(self, x0: int, y0: int, x1: int, y1: int, data: bytes) -> None:
        """Desenha pixels RGB565 LE no retângulo (x0,y0)-(x1,y1), extremos inclusivos."""
        w, h = self.width, self.height
        if not (0 <= x0 <= x1 < w and 0 <= y0 <= y1 < h):
            raise ValueError(f"retângulo ({x0},{y0})-({x1},{y1}) fora da tela {w}x{h}")
        expected = (x1 - x0 + 1) * (y1 - y0 + 1) * 2
        if len(data) != expected:
            raise ValueError(f"esperava {expected} bytes de pixels, recebi {len(data)}")
        self._write(encode_command(Command.DISPLAY_BITMAP, x0, y0, x1, y1))
        step = w * 8
        for i in range(0, len(data), step):
            self._write(data[i : i + step])


class SerialDisplay(BaseDisplay):
    def __init__(self, port: str = PORT_AUTO, serial_factory: Callable | None = None) -> None:
        super().__init__()
        self.port_spec = port
        self._factory = serial_factory
        self._ser = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None

    def open(self) -> None:
        import serial

        port = self.port_spec
        if port in ("", PORT_AUTO):
            port = find_target_port()
            if port is None:
                raise DisplayError(
                    f"Nenhum display {USB_VID:04x}:{USB_PID:04x} encontrado. Confira o cabo e o `lsusb`."
                )
        factory = self._factory or serial.Serial
        try:
            self._ser = factory(port, BAUDRATE, timeout=1, write_timeout=10, rtscts=True)
        except (serial.SerialException, OSError) as exc:
            text = str(exc)
            if "ermission" in text or getattr(exc, "errno", None) == 13:
                raise DisplayError(PERMISSION_HINT.format(port=port)) from exc
            raise DisplayError(f"Não foi possível abrir {port}: {text}") from exc
        self.port = port
        self._hello()

    def close(self) -> None:
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:  # noqa: BLE001 - fechar nunca deve propagar
                pass

    def reset(self) -> None:
        """Reinicia o display; a porta some e volta, o que leva alguns segundos."""
        self._write(encode_command(Command.RESET))
        self.close()
        time.sleep(5)
        self.open()

    def _hello(self) -> None:
        self._write(hello_packet())
        try:
            reply = bytes(self._ser.read(6))
            self._ser.reset_input_buffer()
        except Exception as exc:  # noqa: BLE001
            raise DisplayError(f"Falha ao ler a resposta do display: {exc}") from exc
        self.model, self.panel_w, self.panel_h = HELLO_MODELS.get(reply, UNKNOWN_MODEL)

    def _write(self, data: bytes) -> None:
        import serial

        if self._ser is None:
            raise DisplayError("Display não está conectado.")
        try:
            self._ser.write(data)
        except (serial.SerialException, OSError) as exc:
            self.close()
            raise DisplayError(f"Falha ao escrever no display: {exc}") from exc
        self.bytes_written += len(data)


class SimulatedDisplay(BaseDisplay):
    """Display virtual: o mesmo protocolo, interpretado por um ScreenModel."""

    def __init__(self) -> None:
        super().__init__()
        self.screen = ScreenModel()
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self.screen = ScreenModel()
        self.model = 'Simulador 3.5"'
        self.port = PORT_SIMULATOR
        self._open = True

    def close(self) -> None:
        self._open = False

    def reset(self) -> None:
        self.screen = ScreenModel()

    def _write(self, data: bytes) -> None:
        if not self._open:
            raise DisplayError("Simulador não está aberto.")
        self.screen.feed(data)
        self.bytes_written += len(data)
