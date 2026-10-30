"""Janela principal."""

from __future__ import annotations

import json
from datetime import datetime

from PySide6.QtCore import QMetaObject, QRectF, QSettings, QSize, Qt, QThread, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import APP_TITLE
from .device import PORT_AUTO, PORT_SIMULATOR, list_ports
from .protocol import USB_PID, USB_VID, Orientation, oriented_size
from .scenes import SCENE_CLASSES, RenderContext
from .sysinfo import SystemStats
from .theme import app_icon
from .widgets import Card, SliderRow
from .worker import DisplayWorker

ORIENTATION_LABELS = [
    (Orientation.PORTRAIT, "Retrato · 320×480"),
    (Orientation.REVERSE_PORTRAIT, "Retrato invertido"),
    (Orientation.LANDSCAPE, "Paisagem · 480×320"),
    (Orientation.REVERSE_LANDSCAPE, "Paisagem invertida"),
]

DOT = {"ok": "#34d399", "wait": "#fbbf24", "off": "#6b7686", "err": "#f87171"}


class PreviewWidget(QWidget):
    """Mostra o último quadro dentro de uma moldura que lembra o aparelho."""

    def __init__(self) -> None:
        super().__init__()
        self._image: QImage | None = None
        self.setMinimumSize(300, 300)

    def set_frame(self, image: QImage) -> None:
        self._image = image
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        if self._image is None:
            return
        avail = QRectF(self.rect()).adjusted(18, 18, -18, -18)
        ratio = self._image.width() / self._image.height()
        w, h = avail.width(), avail.width() / ratio
        if h > avail.height():
            w, h = avail.height() * ratio, avail.height()
        screen = QRectF(avail.center().x() - w / 2, avail.center().y() - h / 2, w, h)
        bezel = screen.adjusted(-10, -10, 10, 10)
        p.setPen(QPen(QColor("#2a3340"), 1.5))
        p.setBrush(QColor("#05070a"))
        p.drawRoundedRect(bezel, 18, 18)
        clip = QPainterPath()
        clip.addRoundedRect(screen, 6, 6)
        p.setClipPath(clip)
        p.drawImage(screen, self._image)


def _label(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(True)
    return label


class MainWindow(QMainWindow):
    def __init__(self, simulate: bool = False, port: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1000, 660)
        self.settings = QSettings("35inch-qt", "usbmonitor-qt")
        s = self.settings
        self.stats = SystemStats()

        self.orientation = Orientation(s.value("display/orientation", 0, type=int))
        self.brightness = s.value("display/brightness", 70, type=int)
        self.fps = s.value("content/fps", 2, type=int)
        self.send_enabled = True
        self.partial = s.value("display/partial", True, type=bool)
        self.invert_brightness = s.value("display/invert_brightness", False, type=bool)
        self._connected = False
        self._connecting = False
        self._want_connected = False
        self._last_log = ""
        self._startup_port = PORT_SIMULATOR if simulate else port

        self.scenes = [cls() for cls in SCENE_CLASSES]
        for sc in self.scenes:
            raw = s.value(f"scenes/{sc.key}", "", type=str)
            if raw:
                try:
                    sc.load(json.loads(raw))
                except (ValueError, TypeError):
                    pass
            sc.changed.connect(self._on_scene_changed)
        self.scene_index = min(max(s.value("content/scene", 0, type=int), 0), len(self.scenes) - 1)

        self._start_worker()
        self._build_ui()
        self._refresh_ports()
        self._update_connection_ui()

        self.render_timer = QTimer(self, interval=max(100, 1000 // self.fps), timeout=self._tick)
        self.stats_timer = QTimer(self, interval=1000, timeout=self.stats.sample)
        self.reconnect_timer = QTimer(self, interval=3000, timeout=self._maybe_reconnect)
        for t in (self.render_timer, self.stats_timer, self.reconnect_timer):
            t.start()
        self._tick()

        geometry = s.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        if self._startup_port or s.value("connection/auto_connect", True, type=bool):
            QTimer.singleShot(400, self._auto_connect)

    # ------------------------------------------------------------------ worker
    def _start_worker(self) -> None:
        self.worker_thread = QThread(self)
        self.worker = DisplayWorker()
        self.worker.moveToThread(self.worker_thread)
        self.worker.connection_changed.connect(self._on_connection_changed)
        self.worker.log.connect(self._log)
        self.worker.stats.connect(self._on_stats)
        # o worker é destruído na própria thread quando ela termina
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.start()

    # ---------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(18)

        # esquerda: pré-visualização + status
        left = Card()
        left.setFixedWidth(400)
        title = _label("Pré-visualização", "h1")
        self.preview = PreviewWidget()
        self.status_label = QLabel()
        self.stats_label = _label("", "muted")
        self.connect_btn = QPushButton("Conectar")
        self.connect_btn.setProperty("primary", True)
        self.connect_btn.setMinimumHeight(38)
        self.connect_btn.clicked.connect(self._toggle_connection)
        for w in (title, self.preview, self.status_label, self.stats_label, self.connect_btn):
            left.add(w, 1 if w is self.preview else 0)
        layout.addWidget(left)

        # direita: abas
        tabs = QTabWidget()
        tabs.addTab(self._content_page(), "Conteúdo")
        tabs.addTab(self._display_page(), "Tela")
        tabs.addTab(self._connection_page(), "Conexão")
        layout.addWidget(tabs, 1)

    def _content_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(14)

        seg = QHBoxLayout()
        seg.setSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for i, sc in enumerate(self.scenes):
            b = QPushButton(sc.title)
            b.setCheckable(True)
            b.setProperty("seg", True)
            b.setChecked(i == self.scene_index)
            group.addButton(b, i)
            seg.addWidget(b)
        seg.addStretch(1)
        group.idClicked.connect(self._select_scene)
        v.addLayout(seg)

        card = Card("Ajustes")
        self.stack = QStackedWidget()
        for sc in self.scenes:
            self.stack.addWidget(sc.build_panel())
        self.stack.setCurrentIndex(self.scene_index)
        card.add(self.stack)
        v.addWidget(card)

        rate = Card("Atualização")
        self.fps_row = SliderRow(1, 10, self.fps, " fps")
        self.fps_row.valueChanged.connect(self._set_fps)
        rate.add(self.fps_row)
        rate.add(_label("Só as regiões que mudaram são enviadas ao display, então taxas maiores não pesam em telas estáticas.", "muted"))
        v.addWidget(rate)
        v.addStretch(1)
        return page

    def _display_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(14)

        bright = Card("Brilho")
        self.brightness_row = SliderRow(0, 100, self.brightness, " %")
        self._brightness_timer = QTimer(self, singleShot=True, interval=150, timeout=self._send_brightness)
        self.brightness_row.valueChanged.connect(self._set_brightness)
        bright.add(self.brightness_row)
        v.addWidget(bright)

        orient = Card("Orientação")
        self.orientation_combo = QComboBox()
        for o, text in ORIENTATION_LABELS:
            self.orientation_combo.addItem(text, int(o))
        self.orientation_combo.setCurrentIndex(self.orientation_combo.findData(int(self.orientation)))
        self.orientation_combo.currentIndexChanged.connect(self._set_orientation)
        orient.add(self.orientation_combo)
        orient.add(_label("Dica: use a cena “Teste › Orientação” para conferir se o canto A (vermelho) fica em cima à esquerda.", "muted"))
        v.addWidget(orient)

        actions = Card("Ações")
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, handler, danger in (
            ("Ligar tela", lambda: self.worker.request("screen", True), False),
            ("Desligar tela", lambda: self.worker.request("screen", False), False),
            ("Limpar tela", lambda: self.worker.request("clear"), False),
            ("Reiniciar display…", self._confirm_reset, True),
        ):
            b = QPushButton(text)
            b.setProperty("danger", danger)
            b.clicked.connect(handler)
            row.addWidget(b)
        row.addStretch(1)
        actions.add(row)
        v.addWidget(actions)

        sending = Card("Envio")
        self.send_check = QCheckBox("Enviar quadros ao display")
        self.send_check.setChecked(True)
        self.send_check.toggled.connect(self._set_send_enabled)
        self.partial_check = QCheckBox("Atualização parcial (envia só o que mudou)")
        self.partial_check.setChecked(self.partial)
        self.partial_check.toggled.connect(self._set_partial)
        self.invert_check = QCheckBox("Inverter a escala do brilho (se o controle funcionar ao contrário)")
        self.invert_check.setChecked(self.invert_brightness)
        self.invert_check.toggled.connect(self._set_invert_brightness)
        for c in (self.send_check, self.partial_check, self.invert_check):
            sending.add(c)
        v.addWidget(sending)
        v.addStretch(1)
        return page

    def _connection_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(14)

        dev = Card("Dispositivo")
        row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(280)
        refresh = QPushButton("Atualizar lista")
        refresh.clicked.connect(self._refresh_ports)
        row.addWidget(self.port_combo, 1)
        row.addWidget(refresh)
        dev.add(row)
        self.auto_check = QCheckBox("Conectar automaticamente ao abrir")
        self.auto_check.setChecked(self.settings.value("connection/auto_connect", True, type=bool))
        self.reconnect_check = QCheckBox("Reconectar sozinho se o display for desconectado")
        self.reconnect_check.setChecked(self.settings.value("connection/auto_reconnect", True, type=bool))
        dev.add(self.auto_check)
        dev.add(self.reconnect_check)
        self.info_label = _label("", "muted")
        dev.add(self.info_label)
        self.hint_label = _label(
            "Sem permissão na porta serial. Instale a regra udev que acompanha o projeto "
            "(udev/99-usbmonitor.rules), replugue o display ou adicione seu usuário ao grupo “dialout”.",
            "hint",
        )
        self.hint_label.hide()
        dev.add(self.hint_label)
        v.addWidget(dev)

        logs = Card("Registro")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        logs.add(self.log_view, 1)
        v.addWidget(logs, 1)
        return page

    # ------------------------------------------------------------ render loop
    def _tick(self) -> None:
        w, h = oriented_size(self.orientation)
        ctx = RenderContext(self.stats, datetime.now(), self.orientation)
        image = self.scenes[self.scene_index].render(QSize(w, h), ctx)
        self.preview.set_frame(image)
        if self._connected and self.send_enabled:
            self.worker.push_frame(image)

    def _on_scene_changed(self) -> None:
        sc = self.sender()
        if sc is not None and hasattr(sc, "key"):
            self.settings.setValue(f"scenes/{sc.key}", json.dumps(sc.dump()))
        self._tick()

    def _select_scene(self, index: int) -> None:
        self.scene_index = index
        self.stack.setCurrentIndex(index)
        self.settings.setValue("content/scene", index)
        self._tick()

    def _set_fps(self, fps: int) -> None:
        self.fps = fps
        self.render_timer.setInterval(max(100, 1000 // fps))
        self.settings.setValue("content/fps", fps)

    # ------------------------------------------------------------ tela / brilho
    def _set_brightness(self, level: int) -> None:
        self.brightness = level
        self.settings.setValue("display/brightness", level)
        self._brightness_timer.start()

    def _send_brightness(self) -> None:
        self.worker.request("brightness", self.brightness)

    def _set_orientation(self) -> None:
        self.orientation = Orientation(self.orientation_combo.currentData())
        self.settings.setValue("display/orientation", int(self.orientation))
        self.worker.request("orientation", int(self.orientation))
        self._tick()

    def _set_send_enabled(self, on: bool) -> None:
        self.send_enabled = on
        if on:
            self.worker.request("refresh")
            self._tick()

    def _set_partial(self, on: bool) -> None:
        self.partial = on
        self.settings.setValue("display/partial", on)
        self.worker.request("partial", on)

    def _set_invert_brightness(self, on: bool) -> None:
        self.invert_brightness = on
        self.settings.setValue("display/invert_brightness", on)
        self.worker.request("invert_brightness", {"invert": on, "level": self.brightness})

    def _confirm_reset(self) -> None:
        if not self._connected:
            return
        answer = QMessageBox.question(
            self,
            "Reiniciar display",
            "O display vai reiniciar e a porta serial pode mudar. Isso leva cerca de 5 segundos.\n\nContinuar?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.worker.request("reset", self._state_args())

    # ----------------------------------------------------------------- conexão
    def _refresh_ports(self) -> None:
        current = self.port_combo.currentData() or self.settings.value("connection/port", PORT_AUTO, type=str)
        if self._startup_port:
            current = self._startup_port
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItem(f"Automático ({USB_VID:04x}:{USB_PID:04x})", PORT_AUTO)
        for info in list_ports():
            star = "  ★ UsbMonitor" if info.is_target else ""
            self.port_combo.addItem(f"{info.device} — {info.description}{star}", info.device)
        self.port_combo.addItem("Simulador (sem hardware)", PORT_SIMULATOR)
        idx = self.port_combo.findData(current)
        if idx < 0 and current not in (PORT_AUTO, PORT_SIMULATOR):
            self.port_combo.addItem(f"{current} (não detectada)", current)
            idx = self.port_combo.count() - 1
        self.port_combo.setCurrentIndex(max(0, idx))
        self.port_combo.blockSignals(False)

    def _state_args(self) -> dict:
        return {
            "port": self.port_combo.currentData() or PORT_AUTO,
            "orientation": int(self.orientation),
            "brightness": self.brightness,
            "partial": self.partial,
            "invert_brightness": self.invert_brightness,
        }

    def _auto_connect(self) -> None:
        self._want_connected = True
        self._request_connect()

    def _toggle_connection(self) -> None:
        if self._connected or self._want_connected:
            self._want_connected = False
            self.worker.request("disconnect")
            self._connecting = False
            self._update_connection_ui()
        else:
            self._want_connected = True
            self._request_connect()

    def _request_connect(self) -> None:
        self._connecting = True
        self.settings.setValue("connection/port", self.port_combo.currentData() or PORT_AUTO)
        self.worker.request("connect", self._state_args())
        self._update_connection_ui()

    def _maybe_reconnect(self) -> None:
        if self._want_connected and not self._connected and not self._connecting and self.reconnect_check.isChecked():
            self._request_connect()

    def _on_connection_changed(self, ok: bool, text: str) -> None:
        self._connected = ok
        self._connecting = False
        self._info = text
        if not ok and not self.reconnect_check.isChecked():
            self._want_connected = False
        self.hint_label.setVisible("udev" in text)
        self._update_connection_ui(error=None if ok or text == "Desconectado" else text)
        if ok:
            self._tick()

    def _on_stats(self, fps: float, kib: float, ms: float) -> None:
        self.stats_label.setText(f"{fps:.1f} quadros/s · {kib:.0f} KiB/s · último envio {ms:.0f} ms")

    def _update_connection_ui(self, error: str | None = None) -> None:
        if self._connected:
            dot, text = DOT["ok"], f"Conectado — {getattr(self, '_info', '')}"
        elif self._connecting or self._want_connected:
            dot, text = DOT["wait"], "Procurando o display…" if not error else f"Aguardando — {error}"
        elif error:
            dot, text = DOT["err"], error
        else:
            dot, text = DOT["off"], "Desconectado"
        self.status_label.setText(f'<span style="color:{dot}; font-size:16px;">●</span>&nbsp; {text}')
        self.connect_btn.setText("Desconectar" if (self._connected or self._want_connected) else "Conectar")
        if not self._connected:
            self.stats_label.setText("")
        self.info_label.setText(
            "Os quadros só são enviados enquanto estiver conectado. A pré-visualização funciona sempre."
            if not self._connected
            else f"Resolução do painel: {oriented_size(self.orientation)[0]}×{oriented_size(self.orientation)[1]} · {getattr(self, '_info', '')}"
        )

    def _log(self, message: str) -> None:
        if message == self._last_log:
            return  # evita encher o registro com a mesma falha a cada tentativa de reconexão
        self._last_log = message
        self.log_view.appendPlainText(f"{datetime.now():%H:%M:%S}  {message}")

    # ------------------------------------------------------------------ fechar
    def closeEvent(self, event) -> None:  # noqa: N802
        s = self.settings
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("connection/auto_connect", self.auto_check.isChecked())
        s.setValue("connection/auto_reconnect", self.reconnect_check.isChecked())
        for t in (self.render_timer, self.stats_timer, self.reconnect_timer):
            t.stop()
        QMetaObject.invokeMethod(self.worker, "shutdown", Qt.ConnectionType.BlockingQueuedConnection)
        self.worker_thread.quit()
        self.worker_thread.wait(3000)
        super().closeEvent(event)
