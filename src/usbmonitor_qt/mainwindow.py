"""Janela principal."""

from __future__ import annotations

import json
import time as _time
from datetime import datetime
from datetime import time as dtime

from PySide6.QtCore import QMetaObject, QRectF, QSettings, QSize, Qt, QThread, QTime, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from . import APP_TITLE, autostart, postfx
from .device import PORT_AUTO, PORT_SIMULATOR, list_ports
from .protocol import USB_PID, USB_VID, Orientation, oriented_size
from .scenes import SCENE_CLASSES, RenderContext
from .schedule import carousel_pick, night_active, parse_hhmm
from .sysinfo import SystemStats
from .theme import STATUS_COLORS, app_icon
from .tray import TrayController
from .widgets import Card, SliderRow
from .worker import DisplayWorker

ORIENTATION_LABELS = [
    (Orientation.PORTRAIT, "Retrato · 320×480"),
    (Orientation.REVERSE_PORTRAIT, "Retrato invertido"),
    (Orientation.LANDSCAPE, "Paisagem · 480×320"),
    (Orientation.REVERSE_LANDSCAPE, "Paisagem invertida"),
]
AUTOSTART_OPTIONS = [
    ("off", "Desativado"),
    ("login", "Ao entrar na sessão — janela na bandeja"),
    ("service", "Serviço em segundo plano — sem janela, reinicia sozinho"),
]
AUTOSTART_TEXT = {
    "off": "O aplicativo só roda quando você o abre.",
    "login": "O aplicativo abre minimizado na bandeja ao entrar na sessão.",
    "service": "Serviço systemd de usuário ativo: mantém o display atualizado sem janela.",
}
EXIT_OPTIONS = [
    ("keep", "Manter a última imagem"),
    ("clear", "Limpar a tela"),
    ("off", "Desligar a tela"),
]
CAROUSEL_HOLD_SECONDS = 15  # ao mexer numa cena, a rotação espera para você ver o resultado


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


def _checkbox(text: str, checked: bool, slot) -> QCheckBox:
    box = QCheckBox(text)
    box.setChecked(checked)
    box.toggled.connect(slot)
    return box


def _combo(options: list[tuple[str, str]], current: str, slot) -> QComboBox:
    combo = QComboBox()
    for value, text in options:
        combo.addItem(text, value)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    combo.currentIndexChanged.connect(slot)
    return combo


def _select_silently(combo: QComboBox, value) -> None:
    combo.blockSignals(True)
    combo.setCurrentIndex(max(0, combo.findData(value)))
    combo.blockSignals(False)


def _scrollable(body: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(body)
    return area


def _time_edit(text: str, fallback: dtime, slot) -> QTimeEdit:
    t = parse_hhmm(text, fallback)
    edit = QTimeEdit(QTime(t.hour, t.minute))
    edit.setDisplayFormat("HH:mm")
    edit.timeChanged.connect(slot)
    return edit


class MainWindow(QMainWindow):
    def __init__(self, simulate: bool = False, port: str | None = None, service_mode: bool = False) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1000, 660)
        self.settings = QSettings("35inch-qt", "usbmonitor-qt")
        s = self.settings
        self.service_mode = service_mode  # --headless: sem janela, sem bandeja, sempre reconecta
        self.quit_on_close = False  # o app.py liga; nos testes a janela fecha sem encerrar o Qt
        self._quitting = False
        self._shut = False
        self._now = datetime.now  # trocável nos testes
        self.autostart_runner = autostart.default_runner
        self._autostart_loaded = False
        self._autostart_mode = "off"
        self.stats = SystemStats()

        self.orientation = Orientation(s.value("display/orientation", 0, type=int))
        self.brightness = s.value("display/brightness", 70, type=int)
        self.fps = s.value("content/fps", 2, type=int)
        self.send_enabled = True
        self.partial = s.value("display/partial", True, type=bool)
        self.invert_brightness = s.value("display/invert_brightness", False, type=bool)
        self.warmth = s.value("display/warmth", 0, type=int)
        self.mirror_h = s.value("display/mirror_h", False, type=bool)
        self.mirror_v = s.value("display/mirror_v", False, type=bool)
        self.night_enabled = s.value("night/enabled", False, type=bool)
        self.night_start = s.value("night/start", "22:00", type=str)
        self.night_end = s.value("night/end", "07:00", type=str)
        self.night_mode = s.value("night/mode", "dim", type=str)
        self.night_brightness = s.value("night/brightness", 15, type=int)
        self._night_state = "day"  # "day" | "dim" | "off"
        self.carousel_enabled = s.value("carousel/enabled", False, type=bool)
        self.carousel_interval = s.value("carousel/interval", 10, type=int)
        raw_keys = s.value("carousel/scenes", "dashboard,clock", type=str)
        self.carousel_keys = [k for k in raw_keys.split(",") if k]
        self._carousel_t0 = _time.monotonic()
        self._carousel_hold_until = 0.0
        self.exit_action = s.value("exit/action", "keep", type=str)
        self.tray_enabled = s.value("system/tray_enabled", True, type=bool)
        self.close_to_tray = s.value("system/close_to_tray", True, type=bool)
        self._tray_hint_shown = False

        self._connected = False
        self._connecting = False
        self._want_connected = False
        self._last_log = ""
        self._info = ""
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
        self.tray = TrayController(self)
        self.tray.set_enabled(self.tray_enabled and not self.service_mode)
        self._refresh_ports()
        self._update_connection_ui()

        self.render_timer = QTimer(self, interval=max(100, 1000 // self.fps), timeout=self._tick)
        self.stats_timer = QTimer(self, interval=1000, timeout=self.stats.sample)
        self.reconnect_timer = QTimer(self, interval=3000, timeout=self._maybe_reconnect)
        for t in (self.render_timer, self.stats_timer, self.reconnect_timer):
            t.start()
        self._tick()

        geometry = s.value("window/geometry")
        if geometry is not None and not self.service_mode:
            self.restoreGeometry(geometry)
        if self._startup_port or self.service_mode or s.value("connection/auto_connect", True, type=bool):
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
        self.connect_btn.clicked.connect(self.toggle_connection)
        for w in (title, self.preview, self.status_label, self.stats_label, self.connect_btn):
            left.add(w, 1 if w is self.preview else 0)
        layout.addWidget(left)

        # direita: abas
        self.tabs = QTabWidget()
        self.tabs.addTab(self._content_page(), "Conteúdo")
        self.tabs.addTab(_scrollable(self._display_page()), "Tela")
        self._system_tab_index = self.tabs.addTab(_scrollable(self._system_page()), "Sistema")
        self.tabs.addTab(self._connection_page(), "Conexão")
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, 1)

    def _content_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 6, 0, 0)
        v.setSpacing(14)

        seg = QHBoxLayout()
        seg.setSpacing(8)
        self.scene_group = QButtonGroup(self)
        self.scene_group.setExclusive(True)
        for i, sc in enumerate(self.scenes):
            b = QPushButton(sc.title)
            b.setCheckable(True)
            b.setProperty("seg", True)
            b.setChecked(i == self.scene_index)
            self.scene_group.addButton(b, i)
            seg.addWidget(b)
        seg.addStretch(1)
        self.scene_group.idClicked.connect(self._select_scene)
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
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 6, 12, 6)
        v.setSpacing(14)

        bright = Card("Brilho")
        self.brightness_row = SliderRow(0, 100, self.brightness, " %")
        self._brightness_timer = QTimer(self, singleShot=True, interval=150, timeout=self._send_brightness)
        self.brightness_row.valueChanged.connect(self._set_brightness)
        bright.add(self.brightness_row)
        bright.add(_label("Brilho de dia. Com o modo noturno ativo, na janela de horário vale o brilho definido nele.", "muted"))
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

        look = Card("Aparência")
        look.add(_label("Calor da cor (luz noturna): tinge a imagem de âmbar e cansa menos a vista no escuro.", "muted"))
        self.warmth_row = SliderRow(0, 100, self.warmth, " %")
        self.warmth_row.valueChanged.connect(self._set_warmth)
        look.add(self.warmth_row)
        self.mirror_h_check = _checkbox("Espelhar na horizontal", self.mirror_h, self._set_mirror_h)
        self.mirror_v_check = _checkbox("Espelhar na vertical", self.mirror_v, self._set_mirror_v)
        look.add(self.mirror_h_check)
        look.add(self.mirror_v_check)
        v.addWidget(look)

        night = Card("Modo noturno")
        self.night_check = _checkbox("Ativar no horário abaixo", self.night_enabled, self._set_night_enabled)
        night.add(self.night_check)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.night_start_edit = _time_edit(self.night_start, dtime(22, 0), self._set_night_times)
        self.night_end_edit = _time_edit(self.night_end, dtime(7, 0), self._set_night_times)
        row.addWidget(QLabel("Das"))
        row.addWidget(self.night_start_edit)
        row.addWidget(QLabel("até"))
        row.addWidget(self.night_end_edit)
        row.addStretch(1)
        night.add(row)
        self.night_mode_combo = _combo(
            [("dim", "Reduzir o brilho"), ("off", "Desligar a tela")], self.night_mode, self._set_night_mode
        )
        night.add(self.night_mode_combo)
        self.night_brightness_row = SliderRow(0, 100, self.night_brightness, " %")
        self.night_brightness_row.valueChanged.connect(self._set_night_brightness)
        night.add(self.night_brightness_row)
        self.night_status = _label("", "muted")
        night.add(self.night_status)
        v.addWidget(night)
        self._refresh_night_ui()

        rot = Card("Rotação de cenas")
        self.carousel_check = _checkbox("Alternar entre as cenas marcadas", self.carousel_enabled, self._on_carousel_toggled)
        rot.add(self.carousel_check)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.carousel_spin = QSpinBox()
        self.carousel_spin.setRange(3, 600)
        self.carousel_spin.setSuffix(" s")
        self.carousel_spin.setValue(self.carousel_interval)
        self.carousel_spin.valueChanged.connect(self._set_carousel_interval)
        row.addWidget(QLabel("Trocar a cada"))
        row.addWidget(self.carousel_spin)
        row.addStretch(1)
        rot.add(row)
        scenes_row = QHBoxLayout()
        scenes_row.setSpacing(14)
        self.carousel_boxes: dict[str, QCheckBox] = {}
        for sc in self.scenes:
            box = _checkbox(sc.title, sc.key in self.carousel_keys, lambda on, k=sc.key: self._set_carousel_scene(k, on))
            self.carousel_boxes[sc.key] = box
            scenes_row.addWidget(box)
        scenes_row.addStretch(1)
        rot.add(scenes_row)
        rot.add(_label(f"Ao editar uma cena em Conteúdo, a rotação pausa por {CAROUSEL_HOLD_SECONDS} s para você ver o resultado.", "muted"))
        v.addWidget(rot)

        actions = Card("Ações")
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, handler, danger in (
            ("Ligar tela", lambda: self.screen_power(True), False),
            ("Desligar tela", lambda: self.screen_power(False), False),
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
        self.send_check = _checkbox("Enviar quadros ao display", True, self._set_send_enabled)
        self.partial_check = _checkbox("Atualização parcial (envia só o que mudou)", self.partial, self._set_partial)
        self.invert_check = _checkbox(
            "Inverter a escala do brilho (se o controle funcionar ao contrário)", self.invert_brightness, self._set_invert_brightness
        )
        for c in (self.send_check, self.partial_check, self.invert_check):
            sending.add(c)
        v.addWidget(sending)

        leaving = Card("Ao sair do aplicativo")
        self.exit_combo = _combo(EXIT_OPTIONS, self.exit_action, self._set_exit_action)
        leaving.add(self.exit_combo)
        leaving.add(_label("Com o serviço em segundo plano ativo, ele assume o display logo depois que você fecha a janela.", "muted"))
        v.addWidget(leaving)
        v.addStretch(1)
        return body

    def _system_page(self) -> QWidget:
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 6, 12, 6)
        v.setSpacing(14)

        start = Card("Iniciar com o sistema")
        self.autostart_combo = _combo(AUTOSTART_OPTIONS, "off", self._on_autostart_changed)
        start.add(self.autostart_combo)
        self.autostart_status = _label("Consultando…", "muted")
        start.add(self.autostart_status)
        self.linger_check = _checkbox("Iniciar já no boot, antes de fazer login (linger)", False, self._on_linger_toggled)
        self.linger_check.setEnabled(False)
        start.add(self.linger_check)
        self.service_hint = _label(
            "Sem uma sessão aberta, o acesso à porta serial depende do grupo “dialout”: "
            "rode  sudo usermod -aG dialout $USER  e faça login de novo. "
            "O serviço começa a valer no próximo login (ou boot, com linger) e logo depois que você fechar esta janela.",
            "hint",
        )
        self.service_hint.hide()
        start.add(self.service_hint)
        v.addWidget(start)

        tray = Card("Bandeja do sistema")
        self.tray_check = _checkbox("Mostrar o ícone na bandeja", self.tray_enabled, self._set_tray_enabled)
        self.close_tray_check = _checkbox(
            "Fechar a janela mantém o aplicativo rodando na bandeja", self.close_to_tray, self._set_close_to_tray
        )
        self.tray_status = _label("", "muted")
        for w in (self.tray_check, self.close_tray_check, self.tray_status):
            tray.add(w)
        v.addWidget(tray)
        self._refresh_tray_ui()
        v.addStretch(1)
        return body

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
            "(udev/60-usbmonitor.rules), replugue o display ou adicione seu usuário ao grupo “dialout”.",
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
    def displayed_scene_index(self) -> int:
        """Cena que está de fato no display: a escolhida, ou a da vez na rotação."""
        now = _time.monotonic()
        if self.carousel_enabled and now >= self._carousel_hold_until:
            picked = [i for i, sc in enumerate(self.scenes) if sc.key in self.carousel_keys]
            return carousel_pick(picked, now - self._carousel_t0, self.carousel_interval, self.scene_index)
        return self.scene_index

    def _tick(self) -> None:
        if self.service_mode and not (self._connected and self.send_enabled):
            return  # sem janela e sem display: nada para desenhar
        now = self._now()
        self._apply_night()
        w, h = oriented_size(self.orientation)
        ctx = RenderContext(self.stats, now, self.orientation)
        image = self.scenes[self.displayed_scene_index()].render(QSize(w, h), ctx)
        image = postfx.apply_effects(image, self.warmth, self.mirror_h, self.mirror_v)
        self.preview.set_frame(image)
        if self._connected and self.send_enabled:
            self.worker.push_frame(image)

    def _on_scene_changed(self) -> None:
        sc = self.sender()
        if sc is not None and hasattr(sc, "key"):
            self.settings.setValue(f"scenes/{sc.key}", json.dumps(sc.dump()))
        self._hold_carousel()
        self._tick()

    def _select_scene(self, index: int) -> None:
        self.scene_index = index
        self.stack.setCurrentIndex(index)
        button = self.scene_group.button(index)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        self.settings.setValue("content/scene", index)
        self._hold_carousel()
        self._tick()

    def select_scene_explicit(self, index: int) -> None:
        """Escolha deliberada (bandeja): vale mais que a rotação, que é desligada."""
        if self.carousel_check.isChecked():
            self.carousel_check.setChecked(False)
        self._select_scene(index)

    def _set_fps(self, fps: int) -> None:
        self.fps = fps
        self.render_timer.setInterval(max(100, 1000 // fps))
        self.settings.setValue("content/fps", fps)

    # ---------------------------------------------------------------- rotação
    def _hold_carousel(self) -> None:
        self._carousel_hold_until = _time.monotonic() + CAROUSEL_HOLD_SECONDS
        self._carousel_t0 = self._carousel_hold_until  # depois da pausa, recomeça pela primeira cena

    def _on_carousel_toggled(self, on: bool) -> None:
        self.carousel_enabled = on
        self.settings.setValue("carousel/enabled", on)
        self._carousel_hold_until = 0.0
        self._carousel_t0 = _time.monotonic()
        self._tick()

    def set_carousel(self, on: bool) -> None:
        self.carousel_check.setChecked(on)

    def _set_carousel_interval(self, seconds: int) -> None:
        self.carousel_interval = seconds
        self.settings.setValue("carousel/interval", seconds)

    def _set_carousel_scene(self, key: str, on: bool) -> None:
        keys = [k for k in self.carousel_keys if k != key]
        if on:
            keys.append(key)
        self.carousel_keys = [sc.key for sc in self.scenes if sc.key in keys]  # mantém a ordem das abas
        self.settings.setValue("carousel/scenes", ",".join(self.carousel_keys))

    # ------------------------------------------------------------ tela / brilho
    def effective_brightness(self) -> int:
        return self.night_brightness if self._night_state == "dim" else self.brightness

    def _set_brightness(self, level: int) -> None:
        self.brightness = level
        self.settings.setValue("display/brightness", level)
        self._brightness_timer.start()

    def set_brightness_percent(self, level: int) -> None:
        self.brightness_row.setValue(level)

    def _send_brightness(self) -> None:
        self.worker.request("brightness", self.effective_brightness())

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
        self.worker.request("invert_brightness", {"invert": on, "level": self.effective_brightness()})

    def screen_power(self, on: bool) -> None:
        self.worker.request("screen", on)

    def _set_exit_action(self) -> None:
        self.exit_action = self.exit_combo.currentData()
        self.settings.setValue("exit/action", self.exit_action)

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

    # ---------------------------------------------------------------- aparência
    def _set_warmth(self, value: int) -> None:
        self.warmth = value
        self.settings.setValue("display/warmth", value)
        self._tick()

    def _set_mirror_h(self, on: bool) -> None:
        self.mirror_h = on
        self.settings.setValue("display/mirror_h", on)
        self._tick()

    def _set_mirror_v(self, on: bool) -> None:
        self.mirror_v = on
        self.settings.setValue("display/mirror_v", on)
        self._tick()

    # ------------------------------------------------------------ modo noturno
    def _compute_night_state(self) -> str:
        if not self.night_enabled:
            return "day"
        now = self._now().time()
        start = parse_hhmm(self.night_start, dtime(22, 0))
        end = parse_hhmm(self.night_end, dtime(7, 0))
        if not night_active(now, start, end):
            return "day"
        return "off" if self.night_mode == "off" else "dim"

    def _apply_night(self, force: bool = False) -> None:
        """Envia ao aparelho só o que mudou na transição dia → noite → dia."""
        state = self._compute_night_state()
        changed = state != self._night_state
        previous, self._night_state = self._night_state, state
        if not self._connected or not (changed or force):
            return
        if state == "off":
            self.worker.request("screen", False)
            return
        if changed and previous == "off":
            self.worker.request("screen", True)
        if changed:
            self.worker.request("brightness", self.effective_brightness())

    def _refresh_night_ui(self) -> None:
        dim = self.night_mode == "dim"
        self.night_brightness_row.setEnabled(dim)
        state = self._compute_night_state()
        self.night_status.setText(
            {
                "day": "Fora do horário noturno agora." if self.night_enabled else "Desativado.",
                "dim": "Modo noturno ativo agora: brilho reduzido.",
                "off": "Modo noturno ativo agora: tela desligada.",
            }[state]
        )

    def _set_night_enabled(self, on: bool) -> None:
        self.night_enabled = on
        self.settings.setValue("night/enabled", on)
        self._night_changed()

    def _set_night_times(self) -> None:
        self.night_start = self.night_start_edit.time().toString("HH:mm")
        self.night_end = self.night_end_edit.time().toString("HH:mm")
        self.settings.setValue("night/start", self.night_start)
        self.settings.setValue("night/end", self.night_end)
        self._night_changed()

    def _set_night_mode(self) -> None:
        self.night_mode = self.night_mode_combo.currentData()
        self.settings.setValue("night/mode", self.night_mode)
        self._night_changed()

    def _set_night_brightness(self, level: int) -> None:
        self.night_brightness = level
        self.settings.setValue("night/brightness", level)
        if self._night_state == "dim":
            self._brightness_timer.start()

    def _night_changed(self) -> None:
        self._apply_night()
        self._refresh_night_ui()

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
        night = self._compute_night_state()
        return {
            "port": self.port_combo.currentData() or PORT_AUTO,
            "orientation": int(self.orientation),
            "brightness": self.night_brightness if night == "dim" else self.brightness,
            "partial": self.partial,
            "invert_brightness": self.invert_brightness,
        }

    def is_connected(self) -> bool:
        return self._connected

    def wants_connection(self) -> bool:
        return self._connected or self._want_connected

    def _reconnect_enabled(self) -> bool:
        return self.service_mode or self.reconnect_check.isChecked()

    def _auto_connect(self) -> None:
        self._want_connected = True
        self._request_connect()

    def toggle_connection(self) -> None:
        if self.wants_connection():
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
        if self._want_connected and not self._connected and not self._connecting and self._reconnect_enabled():
            self._request_connect()

    def _on_connection_changed(self, ok: bool, text: str) -> None:
        self._connected = ok
        self._connecting = False
        self._info = text
        if not ok and not self._reconnect_enabled():
            self._want_connected = False
        self.hint_label.setVisible("udev" in text)
        self._update_connection_ui(error=None if ok or text == "Desconectado" else text)
        if ok:
            self._apply_night(force=True)  # o aparelho acabou de ligar: reaplica "tela desligada" se for o caso
            self._tick()

    def _on_stats(self, fps: float, kib: float, ms: float) -> None:
        self.stats_label.setText(f"{fps:.1f} quadros/s · {kib:.0f} KiB/s · último envio {ms:.0f} ms")

    def _update_connection_ui(self, error: str | None = None) -> None:
        if self._connected:
            state, text = "ok", f"Conectado — {self._info}"
        elif self._connecting or self._want_connected:
            state, text = "wait", "Procurando o display…" if not error else f"Aguardando — {error}"
        elif error:
            state, text = "err", error
        else:
            state, text = "off", "Desconectado"
        self.status_label.setText(f'<span style="color:{STATUS_COLORS[state]}; font-size:16px;">●</span>&nbsp; {text}')
        self.connect_btn.setText("Desconectar" if self.wants_connection() else "Conectar")
        if not self._connected:
            self.stats_label.setText("")
        self.info_label.setText(
            "Os quadros só são enviados enquanto estiver conectado. A pré-visualização funciona sempre."
            if not self._connected
            else f"Resolução do painel: {oriented_size(self.orientation)[0]}×{oriented_size(self.orientation)[1]} · {self._info}"
        )
        self.tray.update_state(state, f"35inch-qt — {text}")

    def _log(self, message: str) -> None:
        if message == self._last_log:
            return  # evita encher o registro com a mesma falha a cada tentativa de reconexão
        self._last_log = message
        self.log_view.appendPlainText(f"{datetime.now():%H:%M:%S}  {message}")

    # ----------------------------------------------------- iniciar com o sistema
    def _tab_changed(self, index: int) -> None:
        if index == self._system_tab_index and not self._autostart_loaded:
            self.refresh_autostart_ui()  # consulta o systemd só quando a aba é aberta

    def refresh_autostart_ui(self) -> None:
        st = autostart.status(self.autostart_runner)
        self._autostart_loaded = True
        self._autostart_mode = st.mode
        _select_silently(self.autostart_combo, st.mode)
        item = self.autostart_combo.model().item(self.autostart_combo.findData("service"))
        item.setEnabled(st.systemd_available or st.mode == "service")
        self.linger_check.blockSignals(True)
        self.linger_check.setChecked(bool(st.linger))
        self.linger_check.blockSignals(False)
        self.linger_check.setEnabled(st.mode == "service" and st.linger is not None)
        self.service_hint.setVisible(st.mode == "service")
        note = "" if st.systemd_available else " (o systemd de usuário não está disponível nesta sessão)"
        self.autostart_status.setText(AUTOSTART_TEXT[st.mode] + note)

    def _on_autostart_changed(self) -> None:
        mode = self.autostart_combo.currentData()
        try:
            message = autostart.set_mode(mode, self.autostart_runner)
        except autostart.AutostartError as exc:
            _select_silently(self.autostart_combo, self._autostart_mode)
            self.autostart_status.setText(f"⚠ {exc}")
            self._log(str(exc))
            return
        self._log(message)
        self.refresh_autostart_ui()
        self.autostart_status.setText(message)

    def _on_linger_toggled(self, on: bool) -> None:
        try:
            autostart.set_linger(on, self.autostart_runner)
        except autostart.AutostartError as exc:
            self.linger_check.blockSignals(True)
            self.linger_check.setChecked(not on)
            self.linger_check.blockSignals(False)
            self.autostart_status.setText(f"⚠ {exc}")
            self._log(str(exc))
            return
        self._log("Linger ativado: o serviço sobe no boot." if on else "Linger desativado.")

    # ------------------------------------------------------------------ bandeja
    def _refresh_tray_ui(self) -> None:
        self.close_tray_check.setEnabled(self.tray_enabled)
        self.tray_status.setText(
            "A bandeja está disponível neste ambiente."
            if TrayController.available()  # estático: a bandeja ainda nem foi criada quando a página é montada
            else "A bandeja não está disponível neste ambiente. No GNOME, instale a extensão "
            "“AppIndicator and KStatusNotifierItem Support”; sem ela, fechar a janela encerra o programa."
        )

    def _set_tray_enabled(self, on: bool) -> None:
        self.tray_enabled = on
        self.settings.setValue("system/tray_enabled", on)
        self.tray.set_enabled(on and not self.service_mode)
        self.close_tray_check.setEnabled(on)
        self.tray.refresh()

    def _set_close_to_tray(self, on: bool) -> None:
        self.close_to_tray = on
        self.settings.setValue("system/close_to_tray", on)

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_window(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_window()
        self.tray.refresh()

    # ------------------------------------------------------------------ fechar
    def shutdown(self) -> None:
        """Salva o estado e para a thread de E/S. Pode ser chamado mais de uma vez."""
        if self._shut:
            return
        self._shut = True
        if not self.service_mode:  # o serviço não pode sobrescrever o que a janela guardou
            s = self.settings
            s.setValue("window/geometry", self.saveGeometry())
            s.setValue("connection/auto_connect", self.auto_check.isChecked())
            s.setValue("connection/auto_reconnect", self.reconnect_check.isChecked())
        for t in (self.render_timer, self.stats_timer, self.reconnect_timer, self._brightness_timer):
            t.stop()
        self.worker.exit_action = self.exit_action
        QMetaObject.invokeMethod(self.worker, "shutdown", Qt.ConnectionType.BlockingQueuedConnection)
        self.worker_thread.quit()
        self.worker_thread.wait(3000)
        self.tray.set_enabled(False)

    def quit_app(self) -> None:
        """Encerramento de verdade (menu da bandeja, SIGTERM, outra instância pedindo o display)."""
        self._quitting = True
        self.shutdown()
        if self.quit_on_close:
            QApplication.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._quitting and self.close_to_tray and self.tray.active():
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.notify("35inch-qt continua em execução", "Use o ícone da bandeja para abrir a janela ou sair.")
            self.tray.refresh()
            return
        self.shutdown()
        if self.quit_on_close:
            QApplication.quit()
        super().closeEvent(event)
