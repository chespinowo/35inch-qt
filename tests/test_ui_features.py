"""Integração da janela com modo noturno, rotação de cenas, bandeja, iniciar com o sistema e ação ao sair."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from fake_systemd import FakeSystemd
from PySide6.QtCore import QSettings
from test_ui import dispose, wait_for

from usbmonitor_qt import autostart
from usbmonitor_qt import mainwindow as mw
from usbmonitor_qt.mainwindow import MainWindow
from usbmonitor_qt.protocol import Command, level_to_raw_brightness

NIGHT = datetime(2026, 10, 30, 23, 0)
NOON = datetime(2026, 10, 30, 12, 0)


def make_window(**kwargs):
    QSettings("35inch-qt", "usbmonitor-qt").clear()
    win = MainWindow(**kwargs)
    win.render_timer.stop()
    win.reconnect_timer.stop()
    win.show()
    return win


@pytest.fixture
def window(qapp):
    win = make_window(simulate=True)
    assert wait_for(lambda: win._connected)
    win._tray_hint_shown = True  # a dica de "continua na bandeja" não faz parte destes testes
    yield win
    dispose(win)


def screen_of(win):
    return win.worker._display.screen


def raw(level):
    return level_to_raw_brightness(level)


def set_combo(combo, value):
    combo.setCurrentIndex(combo.findData(value))


# ---------------------------------------------------------------- modo noturno
def test_night_dim_lowers_brightness_and_day_restores_it(window):
    screen = screen_of(window)
    assert wait_for(lambda: screen.brightness_raw == raw(70))
    window._now = lambda: NIGHT
    window.night_brightness_row.setValue(15)
    window.night_check.setChecked(True)
    assert wait_for(lambda: screen.brightness_raw == raw(15))
    assert window.effective_brightness() == 15
    window._now = lambda: NOON
    window._tick()  # é o temporizador de renderização que percebe a virada de horário
    assert wait_for(lambda: screen.brightness_raw == raw(70))
    assert window.effective_brightness() == 70


def test_night_off_turns_the_screen_off_and_back_on(window):
    screen = screen_of(window)
    window._now = lambda: NIGHT
    set_combo(window.night_mode_combo, "off")
    window.night_check.setChecked(True)
    assert wait_for(lambda: screen.screen_on is False)
    window._now = lambda: NOON
    window._tick()
    assert wait_for(lambda: screen.screen_on is True)


def test_night_off_is_reapplied_after_the_display_reconnects(window):
    window._now = lambda: NIGHT
    set_combo(window.night_mode_combo, "off")
    window.night_check.setChecked(True)
    assert wait_for(lambda: screen_of(window).screen_on is False)
    window.toggle_connection()
    assert wait_for(lambda: not window._connected)
    window.toggle_connection()
    assert wait_for(lambda: window._connected)
    # o aparelho novo liga a tela ao conectar; a janela precisa desligá-la de novo
    assert wait_for(lambda: screen_of(window).screen_on is False)


def test_night_disabled_or_outside_window_changes_nothing(window):
    window._now = lambda: NIGHT
    assert window._compute_night_state() == "day"  # modo noturno desligado
    window.night_check.setChecked(True)
    window._now = lambda: NOON
    assert window._compute_night_state() == "day"


def test_night_window_uses_the_configured_times(window):
    window._now = lambda: datetime(2026, 10, 30, 14, 30)
    window.night_check.setChecked(True)
    assert window._compute_night_state() == "day"
    window.night_start_edit.setTime(window.night_start_edit.time().fromString("14:00", "HH:mm"))
    window.night_end_edit.setTime(window.night_end_edit.time().fromString("15:00", "HH:mm"))
    assert window._compute_night_state() == "dim"


def test_connecting_at_night_starts_dimmed(window):
    window._now = lambda: NIGHT
    window.night_brightness_row.setValue(20)
    window.night_check.setChecked(True)
    assert window._state_args()["brightness"] == 20
    window._now = lambda: NOON
    assert window._state_args()["brightness"] == 70


def test_night_settings_persist(window):
    window.night_check.setChecked(True)
    window.night_brightness_row.setValue(33)
    s = window.settings
    assert s.value("night/enabled", False, type=bool) is True
    assert s.value("night/brightness", 0, type=int) == 33


# ----------------------------------------------------------- rotação de cenas
@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(mw, "_time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


def test_carousel_cycles_through_the_selected_scenes(window, clock):
    window.carousel_spin.setValue(10)
    window.carousel_check.setChecked(True)  # começa a contar aqui
    assert [window.scenes[window.displayed_scene_index()].key for _ in (0,)] == ["dashboard"]
    clock[0] += 10
    assert window.scenes[window.displayed_scene_index()].key == "clock"
    clock[0] += 10
    assert window.scenes[window.displayed_scene_index()].key == "dashboard"


def test_carousel_selection_follows_tab_order_and_persists(window, clock):
    window.carousel_boxes["text"].setChecked(True)
    window.carousel_boxes["dashboard"].setChecked(False)
    assert window.carousel_keys == ["clock", "text"]
    assert window.settings.value("carousel/scenes", "", type=str) == "clock,text"


def test_carousel_with_one_scene_stays_put(window, clock):
    window.carousel_boxes["clock"].setChecked(False)  # sobra só "dashboard"
    window.carousel_check.setChecked(True)
    clock[0] += 500
    assert window.displayed_scene_index() == window.scene_index


def test_editing_a_scene_pauses_the_carousel_then_restarts_it(window, clock):
    window.carousel_spin.setValue(10)
    window.carousel_check.setChecked(True)
    window._select_scene(1)  # o usuário abre "Relógio" para ajustar
    assert window.displayed_scene_index() == 1
    clock[0] += mw.CAROUSEL_HOLD_SECONDS - 1
    assert window.displayed_scene_index() == 1  # ainda na pausa
    clock[0] += 1
    assert window.scenes[window.displayed_scene_index()].key == "dashboard"  # recomeça pela primeira
    clock[0] += 10
    assert window.scenes[window.displayed_scene_index()].key == "clock"


def test_changing_a_scene_setting_also_pauses_the_carousel(window, clock):
    window.carousel_check.setChecked(True)
    window._select_scene(1)
    clock[0] += mw.CAROUSEL_HOLD_SECONDS  # a pausa da seleção acabou
    window.scenes[0].set("accent", "#ff0000")
    assert window.displayed_scene_index() == window.scene_index  # editar pausou de novo


def test_explicit_scene_choice_turns_the_carousel_off(window, clock):
    window.carousel_check.setChecked(True)
    window.select_scene_explicit(2)
    assert not window.carousel_check.isChecked() and not window.carousel_enabled
    clock[0] += 1000
    assert window.displayed_scene_index() == 2


def test_carousel_frames_reach_the_display(window, clock):
    window.carousel_spin.setValue(10)
    window.carousel_check.setChecked(True)
    window._tick()
    first = window.preview._image.copy()
    assert wait_for(lambda: screen_of(window).to_qimage() == first.convertToFormat(first.Format.Format_RGB16))
    clock[0] += 10
    window._tick()
    second = window.preview._image.copy()
    assert second != first
    assert wait_for(lambda: screen_of(window).to_qimage() == second.convertToFormat(second.Format.Format_RGB16))


# ------------------------------------------------------- aparência (espelho/cor)
def test_mirror_reaches_the_display(window):
    window._select_scene([s.key for s in window.scenes].index("test"))  # canto A, vermelho, em cima à esquerda
    window.scenes[-1].set("pattern", "orientation")
    window._tick()
    before = window.preview._image
    assert before.pixelColor(2, 2).red() > 200 > before.pixelColor(2, 2).blue()
    window.mirror_h_check.setChecked(True)
    after = window.preview._image
    assert after.pixelColor(after.width() - 3, 2).red() > 200  # o vermelho foi para a direita
    assert after.pixelColor(2, 2).green() > 150  # e o verde (canto B) para a esquerda
    sent = after.convertToFormat(after.Format.Format_RGB16)
    assert wait_for(lambda: screen_of(window).to_qimage() == sent)


def test_warmth_tints_the_frame_sent_to_the_display(window):
    window._select_scene([s.key for s in window.scenes].index("text"))
    window.scenes[window.scene_index].set("bg", "#ffffff")
    window.warmth_row.setValue(100)
    px = window.preview._image.pixelColor(200, 300)
    assert px.red() == 255 and px.blue() < 120  # branco virou âmbar
    sent = window.preview._image.convertToFormat(window.preview._image.Format.Format_RGB16)
    assert wait_for(lambda: screen_of(window).to_qimage() == sent)


# --------------------------------------------------------------- ação ao sair
@pytest.mark.parametrize(
    "action, expect_clear, expect_on",
    [("keep", False, True), ("clear", True, True), ("off", False, False)],
)
def test_exit_action(window, action, expect_clear, expect_on):
    screen = screen_of(window)
    assert wait_for(lambda: screen.brightness_raw is not None)
    set_combo(window.exit_combo, action)
    window.shutdown()
    assert (Command.CLEAR in screen.commands) is expect_clear
    assert screen.screen_on is expect_on
    assert window.settings.value("exit/action", "keep", type=str) == action  # "keep" é o padrão: vale mesmo sem gravar


def test_shutdown_is_idempotent(window):
    window.shutdown()
    window.shutdown()
    assert window.worker_thread.isFinished()


# -------------------------------------------------------------------- bandeja
def test_tray_menu_scene_brightness_and_power_actions(window):
    tray = window.tray
    tray.refresh()
    assert tray.act_on.isEnabled() and tray.act_conn.text() == "Desconectar"

    tray._scene_actions[1].trigger()
    assert window.scene_index == 1 and window.scene_group.button(1).isChecked()

    tray._bright_actions[25].trigger()
    assert window.brightness == 25 and window.brightness_row.value() == 25
    assert wait_for(lambda: screen_of(window).brightness_raw == raw(25))

    tray.act_off.trigger()
    assert wait_for(lambda: screen_of(window).screen_on is False)
    tray.act_on.trigger()
    assert wait_for(lambda: screen_of(window).screen_on is True)


def test_tray_menu_reflects_state_when_opened(window):
    tray = window.tray
    window.set_brightness_percent(80)
    tray.refresh()
    assert tray._bright_actions[75].isChecked()  # o degrau mais próximo
    window.select_scene_explicit(2)
    tray.refresh()
    assert tray._scene_actions[2].isChecked() and not tray.act_carousel.isChecked()
    tray.act_carousel.trigger()
    assert window.carousel_enabled
    tray.refresh()
    assert tray.act_carousel.isChecked() and not any(a.isChecked() for a in tray._scene_actions)


def test_tray_connect_action_toggles_the_connection(window):
    window.tray.act_conn.trigger()
    assert wait_for(lambda: not window._connected)
    window.tray.refresh()
    assert window.tray.act_conn.text() == "Conectar" and not window.tray.act_on.isEnabled()
    window.tray.act_conn.trigger()
    assert wait_for(lambda: window._connected)


def test_tray_toggle_action_hides_and_shows_the_window(window):
    window.tray.act_toggle.trigger()
    assert not window.isVisible()
    window.tray.refresh()
    assert window.tray.act_toggle.text() == "Mostrar janela"
    window.tray.act_toggle.trigger()
    assert window.isVisible()


def test_tray_tooltip_follows_connection_state(window):
    assert "Conectado" in window.tray.icon.toolTip()
    window.toggle_connection()
    assert wait_for(lambda: "Desconectado" in window.tray.icon.toolTip())


def test_closing_goes_to_the_tray_when_one_is_available(window, monkeypatch):
    monkeypatch.setattr(window.tray, "active", lambda: True)
    window.close()
    assert not window.isVisible() and not window._shut
    assert window._connected  # continua atualizando o display em segundo plano
    window.show_window()
    assert window.isVisible()
    window.close_tray_check.setChecked(False)
    window.close()
    assert window._shut


def test_closing_really_quits_without_a_tray(window):
    assert not window.tray.active()  # a plataforma "offscreen" não tem bandeja
    window.close()
    assert window._shut and window.worker_thread.isFinished()


def test_quit_from_the_tray_menu_really_quits_even_with_a_tray(window, monkeypatch):
    monkeypatch.setattr(window.tray, "active", lambda: True)
    window.tray.act_quit.trigger()
    assert window._quitting and window._shut


def test_tray_checkbox_state_and_settings(window):
    window.tray_check.setChecked(False)
    assert not window.close_tray_check.isEnabled()
    assert window.settings.value("system/tray_enabled", True, type=bool) is False
    window.tray_check.setChecked(True)
    assert window.close_tray_check.isEnabled()


# ------------------------------------------------------- iniciar com o sistema
@pytest.fixture
def systemd(window, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    window.autostart_runner = FakeSystemd()
    return window.autostart_runner


def open_system_tab(win):
    win.tabs.setCurrentIndex(win._system_tab_index)


def test_system_tab_loads_status_lazily(window, systemd):
    assert not window._autostart_loaded
    autostart.set_mode("service", systemd)
    open_system_tab(window)
    assert window._autostart_loaded
    assert window.autostart_combo.currentData() == "service"
    assert window.linger_check.isEnabled() and not window.linger_check.isChecked()
    assert not window.service_hint.isHidden()


def test_choosing_service_from_the_ui(window, systemd):
    open_system_tab(window)
    assert window.autostart_combo.currentData() == "off" and not window.linger_check.isEnabled()
    set_combo(window.autostart_combo, "service")
    assert autostart.unit_path().exists() and systemd.enabled
    assert window.autostart_combo.currentData() == "service"
    assert "start" not in systemd.verbs()  # não inicia agora: a janela aberta já usa o display
    window.linger_check.setChecked(True)
    assert systemd.linger == "yes"
    set_combo(window.autostart_combo, "off")
    assert not autostart.unit_path().exists() and not systemd.enabled
    assert not window.linger_check.isEnabled()


def test_choosing_login_autostart_from_the_ui(window, systemd):
    open_system_tab(window)
    set_combo(window.autostart_combo, "login")
    assert autostart.desktop_path().exists() and not autostart.unit_path().exists()


def test_failed_change_reverts_the_combo_and_explains(window, systemd):
    open_system_tab(window)
    window.autostart_runner = FakeSystemd(fail={"enable"})
    set_combo(window.autostart_combo, "service")
    assert window.autostart_combo.currentData() == "off"
    assert window.autostart_status.text().startswith("⚠") and "enable" in window.autostart_status.text()
    assert not autostart.unit_path().exists()
    assert "enable" in window.log_view.toPlainText()


def test_linger_failure_reverts_the_checkbox_with_a_terminal_hint(window, systemd):
    autostart.set_mode("service", systemd)
    open_system_tab(window)
    window.autostart_runner = FakeSystemd(fail={"enable-linger"})
    window.linger_check.setChecked(True)
    assert not window.linger_check.isChecked()
    assert "sudo loginctl enable-linger" in window.autostart_status.text()


def test_service_option_is_disabled_without_systemd(window, systemd, monkeypatch):
    window.autostart_runner = FakeSystemd(available=False)
    open_system_tab(window)
    item = window.autostart_combo.model().item(window.autostart_combo.findData("service"))
    assert not item.isEnabled()
    assert "não está disponível" in window.autostart_status.text()
    set_combo(window.autostart_combo, "login")  # o modo de sessão continua possível
    assert autostart.desktop_path().exists()


# ------------------------------------------------------------- modo de serviço
def test_service_mode_streams_frames_but_never_persists_window_state(qapp):
    win = make_window(simulate=True, service_mode=True)
    try:
        assert wait_for(lambda: win._connected)
        assert not win.tray.active()
        screen = screen_of(win)

        def has_picture():
            img = screen.to_qimage()
            return len({img.pixel(x, y) for x in range(0, img.width(), 9) for y in range(0, img.height(), 9)}) > 3

        assert wait_for(has_picture)
        win.settings.remove("window/geometry")
        win.shutdown()
        assert win.settings.value("window/geometry") is None  # o serviço não sobrescreve o que a janela guardou
    finally:
        dispose(win)


def test_service_mode_keeps_retrying_even_if_reconnect_is_unchecked(qapp):
    win = make_window(port="/dev/definitivamente-nao-existe", service_mode=True)
    try:
        win.reconnect_check.setChecked(False)
        assert wait_for(lambda: "Não foi possível abrir" in win.log_view.toPlainText())
        assert win._want_connected  # um serviço não desiste de achar o display
        assert win.preview._image is None  # e sem display não gasta CPU desenhando
    finally:
        dispose(win)
