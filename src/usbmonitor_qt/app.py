"""Ponto de entrada: `usbmonitor-qt` ou `python -m usbmonitor_qt`."""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from . import APP_TITLE, __version__

INSTANCE_NAME = "35inch-qt"


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="usbmonitor-qt", description=APP_TITLE)
    parser.add_argument("--simulate", action="store_true", help="usa o display simulado (sem hardware)")
    parser.add_argument("--port", help="porta serial a usar (ex.: /dev/ttyACM0); padrão: detectar 1a86:5722")
    parser.add_argument("--list-ports", action="store_true", help="lista as portas seriais e sai")
    parser.add_argument("--minimized", action="store_true", help="inicia na bandeja, sem abrir a janela")
    parser.add_argument(
        "--headless", action="store_true", help="roda como serviço, sem janela nem bandeja (não precisa de ambiente gráfico)"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _wait_for_lock(lock, timeout: float) -> bool:
    from PySide6.QtCore import QCoreApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lock.acquire():
            return True
        QCoreApplication.processEvents()
        time.sleep(0.1)
    return False


def _take_over(args, lock, name: str) -> int | None:
    """Já existe uma instância. Devolve o código de saída, ou None se o display foi liberado e podemos seguir."""
    from PySide6.QtWidgets import QMessageBox

    from .instance import send_command

    reply = send_command(name, "show")
    if reply == "window":
        print("O 35inch-qt já está aberto; a janela existente foi trazida para a frente.")
        return 0
    if reply == "headless":
        if args.headless:
            print("O serviço já está em execução.")
            return 0
        answer = QMessageBox.question(
            None,
            "35inch-qt",
            "O serviço em segundo plano está usando o display.\n\n"
            "Pausar o serviço enquanto esta janela estiver aberta? Ele volta sozinho quando você fechar.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return 0
        send_command(name, "quit")
        if _wait_for_lock(lock, 6.0):
            return None
        print("O serviço não liberou o display a tempo.", file=sys.stderr)
        return 1
    pid = lock.holder_pid()
    print(f"Outra instância (PID {pid}) mantém o display e não respondeu.", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args, qt_args = _parser().parse_known_args(argv)

    if args.list_ports:
        return _list_ports()
    if args.headless:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"  # sem janela: dispensa servidor gráfico

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from . import autostart
    from .instance import InstanceLock, InstanceServer
    from .mainwindow import MainWindow
    from .theme import app_icon, stylesheet

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("35inch-qt")
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)  # quem decide encerrar é a janela (bandeja, Sair, SIGTERM)

    name = INSTANCE_NAME + ("-sim" if args.simulate else "")  # o simulador não disputa com o aparelho real
    lock = InstanceLock(name)
    if not lock.acquire():
        code = _take_over(args, lock, name)
        if code is not None:
            return code

    window = MainWindow(simulate=args.simulate, port=args.port, service_mode=args.headless)
    window.quit_on_close = True

    def handle(command: str) -> str:
        if command == "show":
            if args.headless:
                return "headless"
            window.show_window()
            return "window"
        if command == "quit":
            QTimer.singleShot(0, window.quit_app)
            return "bye"
        return "ok"

    server = InstanceServer(name, handle)
    server.start()

    def cleanup() -> None:
        window.shutdown()
        server.close()
        lock.release()
        # Devolve o display ao serviço (se estiver habilitado) depois de soltar a trava,
        # para ele não encontrar o display ocupado.
        if not args.headless and not args.simulate:
            autostart.start_service_if_enabled()

    app.aboutToQuit.connect(cleanup)

    # Ctrl+C / SIGTERM (systemd): o interpretador só trata sinais quando roda código Python,
    # então um temporizador "acorda" o laço de eventos periodicamente.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: window.quit_app())
    heartbeat = QTimer()
    heartbeat.start(400)
    heartbeat.timeout.connect(lambda: None)

    if args.headless:
        pass  # nada para mostrar
    elif args.minimized and window.tray.active():
        pass  # fica só na bandeja
    elif args.minimized:
        window.showMinimized()  # sem bandeja, uma janela minimizada é a única forma de não incomodar
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
