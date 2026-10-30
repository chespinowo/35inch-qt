"""Coleta de métricas do sistema (CPU, RAM, temperatura, disco, rede)."""

from __future__ import annotations

import os
import socket
import time
from collections import deque

try:
    import psutil
except ImportError:  # pragma: no cover - psutil é dependência, mas o app não deve quebrar sem ele
    psutil = None

_TEMP_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "acpitz")


def fmt_rate(bytes_per_s: float) -> str:
    v = float(bytes_per_s)
    for unit in ("B/s", "KB/s", "MB/s"):
        if v < 1024:
            return f"{v:.0f} {unit}" if unit == "B/s" else f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} GB/s"


def fmt_gb(n_bytes: float) -> str:
    return f"{n_bytes / 1024**3:.1f}"


def fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}min"
    return f"{m}min"


class SystemStats:
    HISTORY = 60

    def __init__(self) -> None:
        self.hostname = socket.gethostname()
        self.cpu = 0.0
        self.cpu_cores = os.cpu_count() or 1
        self.cpu_freq_ghz: float | None = None
        self.ram_pct = 0.0
        self.ram_used = 0
        self.ram_total = 0
        self.temp: float | None = None
        self.disk_pct = 0.0
        self.disk_used = 0
        self.disk_total = 0
        self.net_down = 0.0
        self.net_up = 0.0
        self.uptime = 0.0
        self.down_hist: deque[float] = deque([0.0] * self.HISTORY, maxlen=self.HISTORY)
        self.up_hist: deque[float] = deque([0.0] * self.HISTORY, maxlen=self.HISTORY)
        self._last_net = None
        self._last_t = 0.0
        self._disk_path = os.path.abspath(os.sep)
        if psutil is not None:
            psutil.cpu_percent(None)  # a primeira chamada sempre devolve 0
            self.sample()

    def sample(self) -> None:
        if psutil is None:
            return
        now = time.monotonic()
        self.cpu = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()
        self.ram_pct, self.ram_used, self.ram_total = vm.percent, vm.used, vm.total
        try:
            du = psutil.disk_usage(self._disk_path)
            self.disk_pct, self.disk_used, self.disk_total = du.percent, du.used, du.total
        except OSError:
            pass
        try:
            freq = psutil.cpu_freq()
            self.cpu_freq_ghz = freq.current / 1000 if freq and freq.current else None
        except Exception:  # noqa: BLE001
            self.cpu_freq_ghz = None
        self.temp = self._read_temp()
        try:
            self.uptime = time.time() - psutil.boot_time()
        except Exception:  # noqa: BLE001
            pass
        net = psutil.net_io_counters()
        if self._last_net is not None and now > self._last_t:
            dt = now - self._last_t
            self.net_down = max(0.0, (net.bytes_recv - self._last_net.bytes_recv) / dt)
            self.net_up = max(0.0, (net.bytes_sent - self._last_net.bytes_sent) / dt)
            self.down_hist.append(self.net_down)
            self.up_hist.append(self.net_up)
        self._last_net, self._last_t = net, now

    @staticmethod
    def _read_temp() -> float | None:
        if psutil is None or not hasattr(psutil, "sensors_temperatures"):
            return None
        try:
            data = psutil.sensors_temperatures()
        except Exception:  # noqa: BLE001
            return None
        if not data:
            return None
        for name in _TEMP_SENSORS:
            entries = data.get(name)
            if not entries:
                continue
            for e in entries:
                label = (e.label or "").lower()
                if "package" in label or "tctl" in label:
                    return float(e.current)
            return float(entries[0].current)
        first = next((v for v in data.values() if v), None)
        return float(first[0].current) if first else None
