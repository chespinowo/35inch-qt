"""Instância única: só um processo fala com o display por vez.

* `InstanceLock`   — trava em arquivo (QLockFile), que detecta sozinho um processo morto.
* `InstanceServer` — soquete local por onde uma segunda execução pede "show" (mostrar a janela)
                     ou "quit" (liberar o display) à instância que já está rodando.
* `send_command`   — o lado cliente, bloqueante e com tempo limite.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QLockFile, QObject, QStandardPaths
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def _runtime_dir() -> Path:
    found = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
    return Path(found) if found else Path(tempfile.gettempdir())


def server_name(name: str) -> str:
    uid = os.getuid() if hasattr(os, "getuid") else 0
    return f"{name}-{uid}"


class InstanceLock:
    def __init__(self, name: str, directory: str | Path | None = None) -> None:
        self.name = name
        self.path = Path(directory) if directory else _runtime_dir()
        self.path.mkdir(parents=True, exist_ok=True)
        self._lock = QLockFile(str(self.path / f"{name}.lock"))

    def acquire(self) -> bool:
        return self._lock.tryLock(0)

    def release(self) -> None:
        self._lock.unlock()

    def holder_pid(self) -> int | None:
        info = self._lock.getLockInfo()  # PySide6: (pid, host, app); versões antigas: (ok, pid, host, app)
        if not info:
            return None
        if len(info) == 4:
            ok, *info = info
            if not ok:
                return None
        try:
            return int(info[0])
        except (TypeError, ValueError):
            return None


class InstanceServer(QObject):
    """Atende comandos de curta duração. `handler(comando) -> resposta`."""

    def __init__(self, name: str, handler: Callable[[str], str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = server_name(name)
        self._handler = handler
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)

    def start(self) -> bool:
        # Só chamar depois de adquirir o InstanceLock: um soquete deixado para trás é de um processo morto.
        QLocalServer.removeServer(self._name)
        return self._server.listen(self._name)

    def close(self) -> None:
        self._server.close()
        QLocalServer.removeServer(self._name)

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))
            sock.disconnected.connect(sock.deleteLater)

    def _on_ready(self, sock: QLocalSocket) -> None:
        command = bytes(sock.readAll()).decode("utf-8", "replace").strip()
        try:
            reply = self._handler(command) or "ok"
        except Exception as exc:  # noqa: BLE001 - uma resposta ruim não pode derrubar o app
            reply = f"erro: {exc}"
        sock.write(reply.encode("utf-8"))
        sock.flush()
        sock.disconnectFromServer()


def send_command(name: str, command: str, timeout_ms: int = 1500) -> str | None:
    """Envia `command` à instância em execução. None = ninguém respondeu."""
    sock = QLocalSocket()
    sock.connectToServer(server_name(name))
    if not sock.waitForConnected(timeout_ms):
        return None
    sock.write(command.encode("utf-8"))
    sock.flush()
    if not sock.waitForReadyRead(timeout_ms):
        sock.abort()
        return None
    reply = bytes(sock.readAll()).decode("utf-8", "replace").strip()
    sock.disconnectFromServer()
    return reply
