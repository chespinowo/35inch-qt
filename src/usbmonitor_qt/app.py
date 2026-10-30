"""Ponto de entrada: `usbmonitor-qt` ou `python -m usbmonitor_qt`."""

from __future__ import annotations

import argparse
import sys

from . import APP_TITLE, __version__


def _list_ports() -> int:
    from .device import list_ports

    ports = list_ports()
    if not ports:
        print("Nenhuma porta serial encontrada.")
        return 1
    for p in ports:
        mark = "  <- UsbMonitor" if p.is_target else ""
        ids = f"{p.vid:04x}:{p.pid:04x}" if p.vid is not None else "----:----"
        print(f"{p.device:<16} {ids}  {p.description}{mark}")
    return 0 if any(p.is_target for p in ports) else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="usbmonitor-qt", description=APP_TITLE)
    parser.add_argument("--simulate", action="store_true", help="usa o display simulado (sem hardware)")
    parser.add_argument("--port", help="porta serial a usar (ex.: /dev/ttyACM0); padrão: detectar 1a86:5722")
    parser.add_argument("--list-ports", action="store_true", help="lista as portas seriais e sai")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args, qt_args = parser.parse_known_args(argv)

    if args.list_ports:
        return _list_ports()

    from PySide6.QtWidgets import QApplication

    from .mainwindow import MainWindow
    from .theme import stylesheet

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("35inch-qt")
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    window = MainWindow(simulate=args.simulate, port=args.port)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
