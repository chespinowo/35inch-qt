"""Thread de comunicação: toda a E/S serial acontece aqui, fora da thread da UI.

A UI entrega quadros com `push_frame`; se o display for mais lento que a taxa
de renderização, quadros intermediários são descartados (vale o mais recente).
Os demais comandos entram por `request(nome, argumentos)` e são executados em
ordem, na thread do worker.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QImage

from .device import PORT_SIMULATOR, BaseDisplay, DisplayError, SerialDisplay, SimulatedDisplay
from .sender import FrameSender, SizeMismatch


class DisplayWorker(QObject):
    connection_changed = Signal(bool, str)  # conectado?, descrição ou motivo da falha
    log = Signal(str)
    stats = Signal(float, float, float)  # quadros/s, KiB/s, duração do último envio (ms)

    _kick = Signal()
    _command = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._pending: QImage | None = None
        self._busy = False
        self._last_image: QImage | None = None
        self._display: BaseDisplay | None = None
        self._sender: FrameSender | None = None
        self._partial = True
        self._window: deque[tuple[float, int]] = deque()
        self._last_stats_emit = 0.0
        self.is_connected = False  # lido pela UI; escrito só nesta thread
        self.exit_action = "keep"  # "keep" | "clear" | "off": o que fazer com a tela ao encerrar
        self._kick.connect(self._process, Qt.ConnectionType.QueuedConnection)
        self._command.connect(self._run_command, Qt.ConnectionType.QueuedConnection)

    # ---- API chamada da thread da UI -----------------------------------
    def push_frame(self, image: QImage) -> None:
        if not self.is_connected:
            return
        with self._lock:
            self._pending = image.copy()
            if self._busy:
                return
            self._busy = True
        self._kick.emit()

    def request(self, name: str, args: object = None) -> None:
        self._command.emit(name, args)

    @Slot()
    def shutdown(self) -> None:
        """Chamado com BlockingQueuedConnection ao fechar a janela."""
        if self._display is not None and self.is_connected:
            try:
                if self.exit_action == "clear":
                    self._display.clear()
                elif self.exit_action == "off":
                    self._display.screen_off()
            except DisplayError:
                pass  # saindo de qualquer jeito; o aparelho pode já ter sido desconectado
        self._disconnect(quiet=True)

    # ---- execução na thread do worker ----------------------------------
    @Slot()
    def _process(self) -> None:
        with self._lock:
            image, self._pending = self._pending, None
            if image is None:
                self._busy = False
                return
        self._last_image = image
        self._send(image)
        with self._lock:
            if self._pending is None:
                self._busy = False
                return
        self._kick.emit()

    @Slot(str, object)
    def _run_command(self, name: str, args: object) -> None:
        handler = getattr(self, f"_cmd_{name}", None)
        if handler is None:
            self.log.emit(f"comando interno desconhecido: {name}")
            return
        try:
            handler(args)
        except DisplayError as exc:
            self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - nada pode derrubar a thread
            self._fail(f"erro inesperado em '{name}': {exc!r}")

    # -- comandos --
    def _cmd_connect(self, args: dict) -> None:
        self._disconnect(quiet=True)
        port = args.get("port", "AUTO")
        self._partial = bool(args.get("partial", True))
        display: BaseDisplay = SimulatedDisplay() if port == PORT_SIMULATOR else SerialDisplay(port)
        display.invert_brightness = bool(args.get("invert_brightness", False))
        display.open()
        self._display = display
        self._sender = FrameSender(display, partial=self._partial)
        self._apply_state(args)
        self.is_connected = True
        self._window.clear()
        self.connection_changed.emit(True, f"{display.model} · {display.port}")
        self.log.emit(f"Conectado: {display.model} em {display.port} ({display.panel_w}×{display.panel_h})")

    def _cmd_disconnect(self, _args: object) -> None:
        self._disconnect()

    def _cmd_brightness(self, level: int) -> None:
        if self._display:
            self._display.set_brightness(level)

    def _cmd_invert_brightness(self, args: dict) -> None:
        if self._display:
            self._display.invert_brightness = bool(args["invert"])
            self._display.set_brightness(args["level"])

    def _cmd_orientation(self, orientation: int) -> None:
        if self._display and self._sender:
            self._display.set_orientation(orientation)
            self._resend()

    def _cmd_screen(self, on: bool) -> None:
        if self._display:
            self._display.screen_on() if on else self._display.screen_off()

    def _cmd_clear(self, _args: object) -> None:
        if self._display and self._sender:
            self._display.clear()
            self._resend()

    def _cmd_reset(self, args: dict) -> None:
        if not (self._display and hasattr(self._display, "reset")):
            return
        self.log.emit("Reiniciando o display… (a porta pode mudar, aguarde ~5 s)")
        self.is_connected = False
        self._display.reset()
        self._apply_state(args)
        self.is_connected = True
        self.log.emit("Display reiniciado.")
        self._resend()

    def _cmd_partial(self, enabled: bool) -> None:
        self._partial = bool(enabled)
        if self._sender:
            self._sender.partial = self._partial
            self._sender.invalidate()
            self._resend()

    def _cmd_refresh(self, _args: object) -> None:
        self._resend()

    # -- internos --
    def _apply_state(self, args: dict) -> None:
        d = self._display
        d.set_orientation(args.get("orientation", 0))
        d.set_brightness(args.get("brightness", 70))
        d.screen_on()
        if self._sender:
            self._sender.invalidate()

    def _resend(self) -> None:
        if self._sender:
            self._sender.invalidate()
        if self._last_image is not None:
            self._send(self._last_image)

    def _send(self, image: QImage) -> None:
        if not (self._display and self._sender):
            return
        t0 = time.perf_counter()
        try:
            report = self._sender.send(image)
        except SizeMismatch:
            return  # troca de orientação em andamento; o próximo quadro já vem no tamanho certo
        except DisplayError as exc:
            self._fail(str(exc))
            return
        if not report.rects:
            return
        now = time.monotonic()
        self._window.append((now, report.bytes_sent))
        while self._window and now - self._window[0][0] > 3.0:
            self._window.popleft()
        if now - self._last_stats_emit >= 0.5:
            span = max(now - self._window[0][0], 1.0)
            fps = len(self._window) / span
            kib = sum(b for _, b in self._window) / span / 1024
            self._last_stats_emit = now
            self.stats.emit(fps, kib, (time.perf_counter() - t0) * 1000)

    def _disconnect(self, quiet: bool = False) -> None:
        was = self.is_connected or self._display is not None
        self.is_connected = False
        if self._display is not None:
            self._display.close()
        self._display = self._sender = None
        with self._lock:
            self._pending = None
        if was and not quiet:
            self.connection_changed.emit(False, "Desconectado")
            self.log.emit("Desconectado.")

    def _fail(self, message: str) -> None:
        self._disconnect(quiet=True)
        self.log.emit(message)
        self.connection_changed.emit(False, message)
