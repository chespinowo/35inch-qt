"""Runner falso de `systemctl`/`loginctl`: registra as chamadas e simula o estado do systemd."""

from __future__ import annotations

import subprocess


class FakeSystemd:
    def __init__(self, available: bool = True, enabled: bool = False, linger: str = "no", fail: set[str] | None = None):
        self.available = available
        self.enabled = enabled
        self.linger = linger
        self.fail = fail or set()  # subcomandos que devem falhar: {"enable", "enable-linger", ...}
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> "subprocess.CompletedProcess[str]":
        self.calls.append(cmd)

        def ok(out: str = "") -> "subprocess.CompletedProcess[str]":
            return subprocess.CompletedProcess(cmd, 0, out, "")

        def bad(err: str = "falhou") -> "subprocess.CompletedProcess[str]":
            return subprocess.CompletedProcess(cmd, 1, "", err)

        if cmd[0] == "loginctl":
            verb = cmd[1]
            if verb in self.fail:
                return bad("Interactive authentication required.")
            if verb == "show-user":
                return ok(f"{self.linger}\n")
            self.linger = "yes" if verb == "enable-linger" else "no"
            return ok()
        verb = cmd[2]
        if verb == "show-environment":
            return ok("A=b\n") if self.available else bad("Failed to connect to bus")
        if verb in self.fail:
            return bad(f"{verb} quebrou")
        if verb == "is-enabled":
            return ok("enabled\n") if self.enabled else subprocess.CompletedProcess(cmd, 1, "disabled\n", "")
        if verb == "enable":
            self.enabled = True
        elif verb == "disable":
            self.enabled = False
        return ok()

    def verbs(self) -> list[str]:
        """Só os subcomandos do systemctl, na ordem em que foram chamados."""
        return [c[2] for c in self.calls if c[0] == "systemctl"]
