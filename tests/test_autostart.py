import configparser
import shlex
import sys

import pytest
from fake_systemd import FakeSystemd

from usbmonitor_qt import autostart as a


@pytest.fixture(autouse=True)
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path / "cfg"


def parse_ini(text: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(text)
    return cp


def test_unit_file_is_a_headless_restarting_service():
    cp = parse_ini(a.unit_text())
    service = cp["Service"]
    assert service["ExecStart"].split()[0] == sys.executable
    assert service["ExecStart"].endswith("-m usbmonitor_qt --headless")
    assert service["Restart"] == "on-failure"  # saída limpa (exit 0) não reinicia: é assim que "quit" funciona
    assert cp["Install"]["WantedBy"] == "default.target"
    assert "QT_QPA_PLATFORM=offscreen" in a.unit_text()


def test_desktop_file_is_a_minimized_gui_launch():
    cp = parse_ini(a.desktop_text())
    entry = cp["Desktop Entry"]
    assert entry["Type"] == "Application"
    assert shlex.split(entry["Exec"]) == [sys.executable, "-m", "usbmonitor_qt", "--minimized"]
    assert entry["Terminal"] == "false"


def test_quoting_survives_spaces_and_special_characters():
    command = ["/home/João Silva/meu venv/bin/python", "-m", "usbmonitor_qt"]
    exec_line = parse_ini(a.desktop_text(command))["Desktop Entry"]["Exec"]
    assert shlex.split(exec_line) == [*command, "--minimized"]
    unit_exec = parse_ini(a.unit_text(command))["Service"]["ExecStart"]
    assert shlex.split(unit_exec) == [*command, "--headless"]
    assert a.quote_systemd("50%$x") == "50%%$$x"  # % e $ são especiais em unidades systemd
    assert a.quote_systemd("sem_espaco") == "sem_espaco"


def test_service_mode_installs_enables_but_does_not_start():
    sd = FakeSystemd()
    msg = a.set_mode("service", sd)
    assert a.unit_path().exists() and not a.desktop_path().exists()
    assert sd.verbs() == ["show-environment", "daemon-reload", "enable"]
    assert "start" not in sd.verbs()  # a janela aberta está usando o display: iniciar agora disputaria a porta
    assert "linger" in msg.lower() or "login" in msg


def test_switching_modes_is_exclusive():
    sd = FakeSystemd()
    a.set_mode("service", sd)
    a.set_mode("login", sd)
    assert a.desktop_path().exists() and not a.unit_path().exists()
    assert "disable" in sd.verbs() and sd.enabled is False
    a.set_mode("service", sd)
    assert a.unit_path().exists() and not a.desktop_path().exists()
    a.set_mode("off", sd)
    assert not a.unit_path().exists() and not a.desktop_path().exists()


def test_login_mode_works_without_systemd():
    sd = FakeSystemd(available=False)
    a.set_mode("login", sd)
    assert a.desktop_path().exists()
    a.set_mode("off", sd)
    assert not a.desktop_path().exists()


def test_service_mode_without_systemd_fails_clearly_and_writes_nothing():
    sd = FakeSystemd(available=False)
    with pytest.raises(a.AutostartError, match="systemd"):
        a.set_mode("service", sd)
    assert not a.unit_path().exists() and not a.desktop_path().exists()


def test_systemctl_failure_surfaces_its_message():
    with pytest.raises(a.AutostartError, match="enable quebrou"):
        a.set_mode("service", FakeSystemd(fail={"enable"}))


def test_failed_enable_leaves_no_stray_unit_file():
    with pytest.raises(a.AutostartError):
        a.set_mode("service", FakeSystemd(fail={"enable"}))
    assert not a.unit_path().exists()


def test_failed_switch_to_service_keeps_the_previous_login_mode():
    sd = FakeSystemd()
    a.set_mode("login", sd)
    with pytest.raises(a.AutostartError):
        a.set_mode("service", FakeSystemd(fail={"enable"}))
    assert a.desktop_path().exists()  # o que já funcionava continua funcionando
    assert not a.unit_path().exists()
    assert a.status(FakeSystemd()).mode == "login"


def test_failed_daemon_reload_also_cleans_up():
    with pytest.raises(a.AutostartError, match="daemon-reload"):
        a.set_mode("service", FakeSystemd(fail={"daemon-reload"}))
    assert not a.unit_path().exists()


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError):
        a.set_mode("sempre", FakeSystemd())


def test_status_reports_the_active_mode():
    sd = FakeSystemd()
    assert a.status(sd).mode == "off"
    a.set_mode("login", sd)
    assert a.status(sd).mode == "login"
    a.set_mode("service", sd)
    st = a.status(sd)
    assert st.mode == "service" and st.systemd_available and st.linger is False
    assert a.status(FakeSystemd(available=False)).linger is None


def test_unit_present_but_not_enabled_is_not_service_mode():
    a.set_mode("service", FakeSystemd())
    assert a.status(FakeSystemd(enabled=False)).mode == "off"


def test_linger_toggle_and_failure_message():
    sd = FakeSystemd()
    a.set_linger(True, sd)
    assert a.linger_enabled(sd) is True
    a.set_linger(False, sd)
    assert a.linger_enabled(sd) is False
    with pytest.raises(a.AutostartError, match=r"sudo loginctl enable-linger"):
        a.set_linger(True, FakeSystemd(fail={"enable-linger"}))


def test_default_runner_survives_a_missing_binary():
    result = a.default_runner(["definitivamente-nao-existe-xyz", "--x"])
    assert result.returncode == 127


def test_resume_service_only_when_enabled():
    started = []
    popen = lambda cmd, **kw: started.append(cmd)  # noqa: E731

    assert a.start_service_if_enabled(FakeSystemd(), popen) is False  # sem unidade
    a.set_mode("service", FakeSystemd())
    assert a.start_service_if_enabled(FakeSystemd(enabled=False), popen) is False
    assert a.start_service_if_enabled(FakeSystemd(enabled=True), popen) is True
    assert started == [["systemctl", "--user", "start", "--no-block", a.SERVICE_NAME]]
