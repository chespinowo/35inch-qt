from datetime import datetime

import pytest
from PySide6.QtCore import QEvent, QCoreApplication, QSettings, QSize
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from usbmonitor_qt.mainwindow import MainWindow
from usbmonitor_qt.protocol import Orientation
from usbmonitor_qt.scenes import SCENE_CLASSES, RenderContext
from usbmonitor_qt.sysinfo import SystemStats


def dispose(win):
    """Fecha e destrói a janela de verdade; sem isso o PySide derruba widgets sobrando ao sair do Python."""
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QTest.qWait(50)


def wait_for(cond, timeout_ms=4000):
    waited = 0
    while not cond() and waited < timeout_ms:
        QTest.qWait(25)
        waited += 25
    return cond()


@pytest.fixture
def window(qapp):
    QSettings("35inch-qt", "usbmonitor-qt").clear()
    win = MainWindow(simulate=True)
    win.render_timer.stop()
    win.reconnect_timer.stop()
    win.show()
    assert wait_for(lambda: win._connected), "simulador deveria conectar sozinho"
    yield win
    dispose(win)


def synced_screen(win):
    """Renderiza um quadro e espera o worker entregá-lo ao simulador. Devolve (quadro enviado, tela do simulador)."""
    win._tick()
    sent = win.preview._image.convertToFormat(QImage.Format.Format_RGB16)
    screen = win.worker._display.screen
    wait_for(lambda: screen.to_qimage() == sent)
    return sent, screen


def test_every_scene_renders_correct_size_in_every_orientation(qapp):
    stats = SystemStats()
    for cls in SCENE_CLASSES:
        scene = cls()
        for o in Orientation:
            size = QSize(480, 320) if o.is_landscape else QSize(320, 480)
            img = scene.render(size, RenderContext(stats, datetime(2026, 1, 2, 3, 4, 5), o))
            assert img.size() == size, f"{cls.__name__} {o.name}"
            assert len({img.pixel(x, y) for x in range(0, size.width(), 7) for y in range(0, size.height(), 7)}) > 1


def test_scene_settings_roundtrip_and_ignore_bad_types(qapp):
    from usbmonitor_qt.scenes import DashboardScene

    a = DashboardScene()
    a.cfg["accent"], a.cfg["net"] = "#ff0000", False
    b = DashboardScene()
    b.load(a.dump())
    assert b.cfg == a.cfg
    b.load({"accent": 123, "cpu": "sim", "desconhecida": 1})
    assert b.cfg["accent"] == "#ff0000" and b.cfg["cpu"] is True and "desconhecida" not in b.cfg


def test_simulator_receives_exactly_what_the_preview_shows(window):
    sent, screen = synced_screen(window)
    assert screen.errors == []
    assert screen.to_qimage() == sent


def test_orientation_switch_resizes_display_and_content(window):
    window.orientation_combo.setCurrentIndex(window.orientation_combo.findData(int(Orientation.LANDSCAPE)))
    assert wait_for(lambda: (window.worker._display.screen.width, window.worker._display.screen.height) == (480, 320))
    sent, screen = synced_screen(window)
    assert (screen.width, screen.height) == (480, 320)
    assert screen.errors == []
    assert screen.to_qimage() == sent


def test_scene_switch_and_setting_change_reach_the_display(window):
    window._select_scene([s.key for s in window.scenes].index("text"))
    window.scenes[window.scene_index].set("text", "OLÁ DISPLAY")
    sent, screen = synced_screen(window)
    assert screen.to_qimage() == sent


def test_brightness_and_screen_commands_reach_the_display(window):
    screen = window.worker._display.screen
    window.brightness_row.setValue(100)  # o brilho sai com um pequeno atraso (debounce)
    assert wait_for(lambda: screen.brightness_raw == 0)
    window.worker.request("screen", False)
    assert wait_for(lambda: screen.screen_on is False)
    window.worker.request("screen", True)
    assert wait_for(lambda: screen.screen_on is True)


def test_disconnect_and_reconnect(window):
    window._toggle_connection()
    assert wait_for(lambda: not window._connected)
    assert window.connect_btn.text() == "Conectar"
    window._toggle_connection()
    assert wait_for(lambda: window._connected)
    assert window.connect_btn.text() == "Desconectar"


def test_missing_port_reports_error_and_keeps_trying(qapp):
    QSettings("35inch-qt", "usbmonitor-qt").clear()
    win = MainWindow(port="/dev/definitivamente-nao-existe")
    win.render_timer.stop()
    win.show()
    try:
        assert wait_for(lambda: "Não foi possível abrir" in win.log_view.toPlainText())
        assert not win._connected
        assert win._want_connected  # com reconexão automática ligada, continua tentando
    finally:
        dispose(win)


def test_closing_the_window_stops_the_worker_thread(qapp):
    QSettings("35inch-qt", "usbmonitor-qt").clear()
    win = MainWindow(simulate=True)
    win.show()
    assert wait_for(lambda: win._connected)
    thread = win.worker_thread
    win.close()
    assert thread.isFinished()
    dispose(win)
