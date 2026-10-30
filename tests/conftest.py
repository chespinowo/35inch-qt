import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    # as configurações salvas pelos testes não podem vazar para o usuário
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path_factory.mktemp("qsettings")))
    app = QApplication.instance() or QApplication([])
    from usbmonitor_qt.theme import stylesheet

    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    yield app
