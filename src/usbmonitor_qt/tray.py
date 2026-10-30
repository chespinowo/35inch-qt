"""Ícone na bandeja do sistema, com o status do display e um menu de atalhos.

Em KDE, XFCE, Cinnamon, MATE etc. funciona direto. No GNOME é preciso a extensão
"AppIndicator and KStatusNotifierItem Support"; sem ela `available()` devolve False e o
aplicativo se comporta como uma janela comum (fechar a janela encerra o programa).
"""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .theme import app_icon

BRIGHTNESS_STEPS = (10, 25, 50, 75, 100)


class TrayController(QObject):
    """Fala com a janela principal só por métodos públicos (ver `MainWindow`)."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.icon = QSystemTrayIcon(app_icon("off"), self)
        self.icon.setToolTip("35inch-qt")
        self.menu = QMenu()  # sem pai: é do ícone, não da janela
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._on_activated)
        self.menu.aboutToShow.connect(self.refresh)
        self._build()

    # ------------------------------------------------------------ estado
    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def active(self) -> bool:
        """Há mesmo um ícone visível para o usuário?"""
        return self.available() and self.icon.isVisible()

    def set_enabled(self, on: bool) -> None:
        if on and self.available():
            self.icon.show()
        else:
            self.icon.hide()

    def update_state(self, state: str, tooltip: str) -> None:
        self.icon.setIcon(app_icon(state))
        self.icon.setToolTip(tooltip)

    def notify(self, title: str, message: str) -> None:
        if self.active():
            self.icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)

    # -------------------------------------------------------------- menu
    def _build(self) -> None:
        m = self.menu
        self.act_toggle = m.addAction("Mostrar janela", self.window.toggle_window)
        m.addSeparator()

        scenes = m.addMenu("Cena")
        self._scene_group = QActionGroup(self)
        self._scene_group.setExclusive(True)
        self._scene_actions: list[QAction] = []
        for i, scene in enumerate(self.window.scenes):
            action = scenes.addAction(scene.title)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, idx=i: self.window.select_scene_explicit(idx))
            self._scene_group.addAction(action)
            self._scene_actions.append(action)
        scenes.addSeparator()
        self.act_carousel = scenes.addAction("Rotação automática")
        self.act_carousel.setCheckable(True)
        self.act_carousel.triggered.connect(self.window.set_carousel)

        bright = m.addMenu("Brilho")
        self._bright_group = QActionGroup(self)
        self._bright_group.setExclusive(True)
        self._bright_actions: dict[int, QAction] = {}
        for level in BRIGHTNESS_STEPS:
            action = bright.addAction(f"{level} %")
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, v=level: self.window.set_brightness_percent(v))
            self._bright_group.addAction(action)
            self._bright_actions[level] = action

        m.addSeparator()
        self.act_on = m.addAction("Ligar tela", lambda: self.window.screen_power(True))
        self.act_off = m.addAction("Desligar tela", lambda: self.window.screen_power(False))
        self.act_conn = m.addAction("Conectar", self.window.toggle_connection)
        m.addSeparator()
        self.act_quit = m.addAction("Sair", self.window.quit_app)

    def refresh(self) -> None:
        w = self.window
        visible = w.isVisible() and not w.isMinimized()
        self.act_toggle.setText("Ocultar janela" if visible else "Mostrar janela")
        for i, action in enumerate(self._scene_actions):
            action.setChecked(i == w.displayed_scene_index() and not w.carousel_enabled)
        self.act_carousel.setChecked(w.carousel_enabled)
        nearest = min(BRIGHTNESS_STEPS, key=lambda v: abs(v - w.effective_brightness()))
        for level, action in self._bright_actions.items():
            action.setChecked(level == nearest)
        self.act_conn.setText("Desconectar" if w.wants_connection() else "Conectar")
        self.act_on.setEnabled(w.is_connected())
        self.act_off.setEnabled(w.is_connected())

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.window.toggle_window()
