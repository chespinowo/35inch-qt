"""Gera as capturas de tela do README usando o modo simulado (sem hardware).

    python scripts/make_screenshots.py [pasta_de_saida]

Roda com a plataforma Qt "offscreen" e dados de sistema fixos, para o resultado ser reprodutível.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="35inch-qt-shots-")  # não mexe nas suas configurações

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QCoreApplication, QEvent, QSize, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QTabWidget  # noqa: E402

from usbmonitor_qt.mainwindow import MainWindow  # noqa: E402
from usbmonitor_qt.protocol import Orientation  # noqa: E402
from usbmonitor_qt.scenes import RenderContext  # noqa: E402
from usbmonitor_qt.theme import stylesheet  # noqa: E402


def pump(ms: int) -> None:
    loop_end = QTimer()
    loop_end.setSingleShot(True)
    loop_end.start(ms)
    while loop_end.isActive():
        QCoreApplication.processEvents()


def demo_stats(stats) -> None:
    stats.hostname = "meu-pc"
    stats.cpu, stats.cpu_freq_ghz = 37.0, 3.4
    stats.ram_pct, stats.ram_used, stats.ram_total = 62.0, int(19.8 * 1024**3), int(31.9 * 1024**3)
    stats.temp = 54.0
    stats.disk_pct, stats.disk_used, stats.disk_total = 44.0, int(412 * 1024**3), int(931 * 1024**3)
    stats.uptime = 3 * 86400 + 4 * 3600
    stats.net_down, stats.net_up = 1.8 * 1024**2, 142 * 1024
    stats.down_hist.clear()
    stats.down_hist.extend(max(0.0, (math.sin(i / 5) + 1.2) * 600 * 1024 + (i % 7) * 90 * 1024) for i in range(60))
    stats.sample = lambda: None  # congela os valores


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "docs")
    out.mkdir(parents=True, exist_ok=True)

    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    win = MainWindow(simulate=True)
    demo_stats(win.stats)
    win.stats_timer.stop()
    win.render_timer.stop()
    win.reconnect_timer.stop()
    win.resize(1080, 700)
    win.show()
    pump(800)  # deixa o simulador conectar
    assert win._connected, "o simulador deveria estar conectado"

    tab_widget = win.findChild(QTabWidget)

    def frame(name: str, scene: str, orientation: Orientation = Orientation.PORTRAIT) -> None:
        idx = [s.key for s in win.scenes].index(scene)
        win._select_scene(idx)
        win.orientation = orientation
        win.orientation_combo.setCurrentIndex(win.orientation_combo.findData(int(orientation)))
        pump(200)
        w, h = (480, 320) if orientation.is_landscape else (320, 480)
        ctx = RenderContext(win.stats, datetime(2026, 10, 29, 20, 13, 42), orientation)
        win.scenes[idx].render(QSize(w, h), ctx).save(str(out / f"display-{name}.png"))

    frame("dashboard-portrait", "dashboard")
    frame("dashboard-landscape", "dashboard", Orientation.LANDSCAPE)
    frame("clock", "clock")
    frame("test-orientation", "test")

    win.orientation = Orientation.PORTRAIT
    win.orientation_combo.setCurrentIndex(0)
    win._select_scene(0)
    for i, name in enumerate(("content", "display", "connection")):
        tab_widget.setCurrentIndex(i)
        pump(300)
        win.grab().save(str(out / f"app-{name}.png"))
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    print("ok ->", out)


if __name__ == "__main__":
    main()
