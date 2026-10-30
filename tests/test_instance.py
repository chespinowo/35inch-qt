import os
import subprocess
import sys
import uuid
from pathlib import Path

from PySide6.QtTest import QTest

from usbmonitor_qt.instance import InstanceLock, InstanceServer, send_command


SRC = Path(__file__).resolve().parents[1] / "src"


def unique(prefix: str) -> str:
    return f"{prefix}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


_CLIENT = (
    "import sys; sys.path.insert(0, {src!r});"
    "from PySide6.QtCore import QCoreApplication; _a = QCoreApplication([]);"
    "from usbmonitor_qt.instance import send_command;"
    "print(repr(send_command({name!r}, {cmd!r}, {timeout})))"
)


def call_from_process(name: str, command: str, timeout_ms: int = 3000):
    """A segunda execução do programa é outro processo; testamos esse caminho, atendendo o servidor aqui."""
    code = _CLIENT.format(src=str(SRC), name=name, cmd=command, timeout=timeout_ms)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    waited = 0
    while proc.poll() is None and waited < 8000:
        QTest.qWait(10)  # mantém o laço de eventos do servidor rodando
        waited += 10
    out = (proc.stdout.read() or "").strip()
    return None if out == "None" else out.strip("'")


def test_lock_is_exclusive_and_released(qapp, tmp_path):
    name = unique("lock")
    first, second = InstanceLock(name, tmp_path), InstanceLock(name, tmp_path)
    assert first.acquire()
    assert not second.acquire()
    assert second.holder_pid() == os.getpid()
    first.release()
    assert second.acquire()
    second.release()


def test_lock_directory_is_created(qapp, tmp_path):
    lock = InstanceLock("x", tmp_path / "a" / "b")
    assert lock.acquire()
    lock.release()


def test_server_answers_commands(qapp):
    name = unique("srv")
    seen = []

    def handler(cmd: str) -> str:
        seen.append(cmd)
        return {"show": "window"}.get(cmd, "")  # resposta vazia vira "ok"

    server = InstanceServer(name, handler)
    assert server.start()
    try:
        assert call_from_process(name, "show") == "window"
        assert call_from_process(name, "qualquer") == "ok"
        assert seen == ["show", "qualquer"]
    finally:
        server.close()


def test_handler_errors_do_not_kill_the_server(qapp):
    name = unique("err")

    def handler(cmd: str) -> str:
        if cmd == "boom":
            raise RuntimeError("quebrou")
        return "vivo"

    server = InstanceServer(name, handler)
    assert server.start()
    try:
        assert call_from_process(name, "boom").startswith("erro:")
        assert call_from_process(name, "oi") == "vivo"
    finally:
        server.close()


def test_nobody_listening_returns_none_quickly(qapp):
    assert send_command(unique("ninguem"), "show", timeout_ms=300) is None


def test_stale_socket_is_replaced(qapp):
    name = unique("stale")
    first = InstanceServer(name, lambda c: "primeiro")
    assert first.start()
    second = InstanceServer(name, lambda c: "segundo")  # quem chama já tem a trava, então o soquete antigo é lixo
    assert second.start()
    try:
        assert call_from_process(name, "x") == "segundo"
    finally:
        second.close()
        first.close()
