"""Iniciar com o sistema: serviço systemd de usuário ou autostart da sessão.

Dois modos, mutuamente exclusivos (dois processos disputando a mesma porta serial dariam conflito):

* ``login``   — arquivo em ~/.config/autostart/. Abre o aplicativo ao entrar na sessão,
                minimizado na bandeja. Funciona em praticamente qualquer ambiente gráfico.
* ``service`` — unidade em ~/.config/systemd/user/. Roda ``--headless`` (sem janela), reinicia
                sozinha se cair e, com "linger" ligado, sobe já no boot, antes do login.

Nada aqui exige root. Os comandos externos passam por um `runner` injetável, o que permite
testar sem systemd.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SERVICE_NAME = "35inch-qt.service"
DESKTOP_NAME = "35inch-qt.desktop"
MODES = ("off", "login", "service")

Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


class AutostartError(RuntimeError):
    """Falha ao configurar a inicialização; a mensagem diz o que fazer."""


def default_runner(cmd: list[str]) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:  # binário ausente, timeout...
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


# ---------------------------------------------------------------------------
# caminhos e comando
# ---------------------------------------------------------------------------
def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def unit_path() -> Path:
    return config_home() / "systemd" / "user" / SERVICE_NAME


def desktop_path() -> Path:
    return config_home() / "autostart" / DESKTOP_NAME


def app_command() -> list[str]:
    """Interpretador atual + módulo: funciona em venv e instalação editável, sem depender do PATH."""
    return [sys.executable, "-m", "usbmonitor_qt"]


# ---------------------------------------------------------------------------
# escrita dos arquivos
# ---------------------------------------------------------------------------
def quote_systemd(arg: str) -> str:
    arg = arg.replace("%", "%%").replace("$", "$$")
    if arg and not any(c in arg for c in ' \t"\'\\;'):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def quote_desktop(arg: str) -> str:
    if arg and not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`%"):
        return arg
    escaped = arg
    for ch in ("\\", '"', "`", "$"):  # nível do campo Exec
        escaped = escaped.replace(ch, "\\" + ch)
    escaped = escaped.replace("\\", "\\\\").replace("%", "%%")  # nível do valor da chave
    return f'"{escaped}"'


def unit_text(command: list[str] | None = None) -> str:
    exec_line = " ".join(quote_systemd(a) for a in [*(command or app_command()), "--headless"])
    return (
        "[Unit]\n"
        'Description=35inch-qt - display USB de 3.5" (UsbMonitor)\n'
        "Documentation=https://github.com/chespinowo/35inch-qt\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_line}\n"
        "Environment=QT_QPA_PLATFORM=offscreen\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def desktop_text(command: list[str] | None = None) -> str:
    exec_line = " ".join(quote_desktop(a) for a in [*(command or app_command()), "--minimized"])
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=35inch-qt\n"
        'Comment=Controle do display USB de 3.5" (UsbMonitor)\n'
        f"Exec={exec_line}\n"
        "Icon=35inch-qt\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
        "X-GNOME-Autostart-Delay=3\n"
    )


def _write(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise AutostartError(f"Não foi possível gravar {path}: {exc}") from exc


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise AutostartError(f"Não foi possível remover {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# consulta
# ---------------------------------------------------------------------------
@dataclass
class Status:
    mode: str  # "off" | "login" | "service"
    systemd_available: bool
    linger: bool | None  # None = não foi possível consultar


def systemd_available(runner: Runner = default_runner) -> bool:
    return runner(["systemctl", "--user", "show-environment"]).returncode == 0


def service_enabled(runner: Runner = default_runner) -> bool:
    if not unit_path().exists():
        return False
    result = runner(["systemctl", "--user", "is-enabled", SERVICE_NAME])
    return result.returncode == 0 and result.stdout.strip() == "enabled"


def linger_enabled(runner: Runner = default_runner) -> bool | None:
    result = runner(["loginctl", "show-user", getpass.getuser(), "--property=Linger", "--value"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() == "yes"


def status(runner: Runner = default_runner) -> Status:
    available = systemd_available(runner)
    if available and service_enabled(runner):
        mode = "service"
    elif desktop_path().exists():
        mode = "login"
    else:
        mode = "off"
    return Status(mode, available, linger_enabled(runner) if available else None)


# ---------------------------------------------------------------------------
# alteração
# ---------------------------------------------------------------------------
def _check(result: "subprocess.CompletedProcess[str]", what: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"código {result.returncode}"
        raise AutostartError(f"systemctl {what} falhou: {detail}")


def _drop_service(runner: Runner) -> None:
    if not unit_path().exists():
        return
    if systemd_available(runner):
        runner(["systemctl", "--user", "disable", "--now", SERVICE_NAME])  # melhor esforço
    _remove(unit_path())
    if systemd_available(runner):
        runner(["systemctl", "--user", "daemon-reload"])


def set_mode(mode: str, runner: Runner = default_runner, command: list[str] | None = None) -> str:
    """Aplica o modo e devolve uma mensagem para o usuário. Levanta AutostartError se falhar."""
    if mode not in MODES:
        raise ValueError(f"modo inválido: {mode!r}")
    if mode == "service":
        if not systemd_available(runner):
            raise AutostartError(
                "O systemd de usuário não está disponível nesta sessão. Use “Ao entrar na sessão”."
            )
        _write(unit_path(), unit_text(command))
        try:
            _check(runner(["systemctl", "--user", "daemon-reload"]), "daemon-reload")
            _check(runner(["systemctl", "--user", "enable", SERVICE_NAME]), "enable")
        except AutostartError:
            _remove(unit_path())  # não deixa uma unidade solta que o usuário não ativou
            runner(["systemctl", "--user", "daemon-reload"])  # melhor esforço
            raise  # o modo anterior (ex.: login) continua intacto
        _remove(desktop_path())  # só agora: dois iniciadores disputariam a mesma porta serial
        return (
            "Serviço ativado. Ele começa no próximo login (ou no boot, com “linger”) "
            "e também assim que você fechar esta janela."
        )
    if mode == "login":
        _drop_service(runner)
        _write(desktop_path(), desktop_text(command))
        return "Autostart ativado: o aplicativo abre minimizado na bandeja ao entrar na sessão."
    _drop_service(runner)
    _remove(desktop_path())
    return "Inicialização automática desativada."


def set_linger(enabled: bool, runner: Runner = default_runner) -> None:
    result = runner(["loginctl", "enable-linger" if enabled else "disable-linger"])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        verb = "enable" if enabled else "disable"
        raise AutostartError(
            f"Não foi possível alterar o linger{': ' + detail if detail else ''}. "
            f"Rode no terminal: sudo loginctl {verb}-linger {getpass.getuser()}"
        )


def start_service_if_enabled(
    runner: Runner = default_runner, popen: Callable[..., object] = subprocess.Popen
) -> bool:
    """Ao fechar a janela, devolve o display ao serviço (se o modo serviço estiver ativo)."""
    if not service_enabled(runner):
        return False
    popen(
        ["systemctl", "--user", "start", "--no-block", SERVICE_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True
