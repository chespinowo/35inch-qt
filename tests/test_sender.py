import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from usbmonitor_qt.device import DisplayError, SerialDisplay, SimulatedDisplay
from usbmonitor_qt.imaging import to_rgb565_rows
from usbmonitor_qt.protocol import Command, Orientation
from usbmonitor_qt.sender import FrameSender, SizeMismatch, diff_rects
from usbmonitor_qt.simulator import ScreenModel


def make_image(w=320, h=480, color="#102030"):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    return img


def paint_rect(img, rect, color):
    p = QPainter(img)
    p.fillRect(QRect(*rect), QColor(color))
    p.end()


class FakeSerial:
    """Porta serial falsa: o que for escrito alimenta um ScreenModel."""

    def __init__(self, port, baud, hello=bytes([1]) * 6, **kwargs):
        self.port, self.baud, self.kwargs = port, baud, kwargs
        self.screen = ScreenModel()
        self.hello = hello
        self.closed = False
        self.fail = False

    def write(self, data):
        if self.fail:
            import serial

            raise serial.SerialException("device disconnected")
        self.screen.feed(data)
        return len(data)

    def read(self, n):
        return self.hello[:n]

    def reset_input_buffer(self):
        pass

    def close(self):
        self.closed = True


def test_diff_rects_cases(qapp):
    a = make_image()
    b = a.copy()
    rows_a = to_rgb565_rows(a)
    assert diff_rects(rows_a, to_rgb565_rows(b)) == []
    assert diff_rects(None, rows_a) == [(0, 0, 319, 479)]
    paint_rect(b, (10, 20, 5, 3), "#ffffff")  # x=10..14, y=20..22
    assert diff_rects(rows_a, to_rgb565_rows(b)) == [(10, 20, 14, 22)]
    paint_rect(b, (300, 470, 20, 10), "#ff0000")  # canto inferior direito, longe da primeira
    assert diff_rects(rows_a, to_rgb565_rows(b)) == [(10, 20, 14, 22), (300, 470, 319, 479)]


def test_diff_rects_merges_close_rows_but_not_distant_ones(qapp):
    a = make_image()
    rows_a = to_rgb565_rows(a)
    b = a.copy()
    paint_rect(b, (10, 100, 5, 2), "#ffffff")  # y=100..101
    paint_rect(b, (200, 104, 5, 2), "#ffffff")  # y=104..105: gap de 2 linhas -> mesma faixa
    assert diff_rects(rows_a, to_rgb565_rows(b)) == [(10, 100, 204, 105)]
    c = a.copy()
    paint_rect(c, (10, 100, 5, 2), "#ffffff")
    paint_rect(c, (200, 140, 5, 2), "#ffffff")  # longe -> faixas separadas, cada uma só com suas colunas
    assert diff_rects(rows_a, to_rgb565_rows(c)) == [(10, 100, 14, 101), (200, 140, 204, 141)]


def test_serial_display_handshake_and_full_frame(qapp):
    fakes = []

    def factory(port, baud, **kw):
        fakes.append(FakeSerial(port, baud, **kw))
        return fakes[-1]

    d = SerialDisplay(port="/dev/ttyACM0", serial_factory=factory)
    d.open()
    assert d.model == 'UsbMonitor 3.5"' and (d.panel_w, d.panel_h) == (320, 480)
    assert fakes[0].baud == 115200 and fakes[0].kwargs["rtscts"] is True
    d.set_orientation(Orientation.PORTRAIT)
    d.set_brightness(100)
    d.screen_on()
    img = make_image()
    paint_rect(img, (40, 60, 100, 50), "#ff8800")
    FrameSender(d).send(img)
    screen = fakes[0].screen
    assert screen.errors == []
    assert screen.brightness_raw == 0
    expected = img.convertToFormat(QImage.Format.Format_RGB16)
    assert screen.to_qimage() == expected


def test_unknown_hello_falls_back_to_turing_35(qapp):
    d = SerialDisplay(port="x", serial_factory=lambda p, b, **kw: FakeSerial(p, b, hello=b"", **kw))
    d.open()
    assert (d.panel_w, d.panel_h) == (320, 480) and "Turing" in d.model


def test_partial_updates_send_less_and_stay_correct(qapp):
    d = SimulatedDisplay()
    d.open()
    sender = FrameSender(d)
    img = make_image()
    first = sender.send(img)
    assert first.full and first.bytes_sent > 320 * 480 * 2

    assert sender.send(img).rects == []  # nada mudou, nada enviado

    paint_rect(img, (100, 200, 30, 12), "#00ff88")
    small = sender.send(img)
    assert not small.full and small.rects == [(100, 200, 129, 211)]
    assert small.bytes_sent < 2000

    assert d.screen.to_qimage() == img.convertToFormat(QImage.Format.Format_RGB16)
    assert d.screen.errors == []


def test_large_change_falls_back_to_full_frame(qapp):
    d = SimulatedDisplay()
    d.open()
    sender = FrameSender(d)
    img = make_image()
    sender.send(img)
    paint_rect(img, (0, 0, 320, 400), "#ffffff")
    assert sender.send(img).full


def test_partial_disabled_always_sends_full_frame(qapp):
    d = SimulatedDisplay()
    d.open()
    sender = FrameSender(d, partial=False)
    img = make_image()
    sender.send(img)
    assert sender.send(img).full


@pytest.mark.parametrize("o", list(Orientation))
def test_orientation_changes_frame_size(qapp, o):
    d = SimulatedDisplay()
    d.open()
    d.set_orientation(o)
    w, h = (480, 320) if o.is_landscape else (320, 480)
    assert (d.width, d.height) == (w, h)
    sender = FrameSender(d)
    img = make_image(w, h)
    paint_rect(img, (5, 5, 50, 20), "#ff00ff")
    sender.send(img)
    assert d.screen.errors == []
    assert d.screen.to_qimage() == img.convertToFormat(QImage.Format.Format_RGB16)


def test_wrong_size_frame_is_rejected(qapp):
    d = SimulatedDisplay()
    d.open()
    with pytest.raises(SizeMismatch):
        FrameSender(d).send(make_image(480, 320))


def test_clear_goes_through_portrait_and_restores_orientation(qapp):
    d = SimulatedDisplay()
    d.open()
    d.set_orientation(Orientation.LANDSCAPE)
    d.clear()
    assert d.orientation == Orientation.LANDSCAPE
    assert d.screen.commands.count(Command.SET_ORIENTATION) == 3  # landscape, portrait, landscape
    assert Command.CLEAR in d.screen.commands


def test_write_failure_becomes_display_error_and_closes(qapp):
    fake = FakeSerial("x", 115200)
    d = SerialDisplay(port="x", serial_factory=lambda p, b, **kw: fake)
    d.open()
    fake.fail = True
    with pytest.raises(DisplayError):
        d.screen_on()
    assert fake.closed and not d.is_open


def test_permission_denied_has_actionable_message(qapp):
    import serial

    def factory(port, baud, **kw):
        raise serial.SerialException("[Errno 13] could not open port: Permission denied")

    d = SerialDisplay(port="/dev/ttyACM0", serial_factory=factory)
    with pytest.raises(DisplayError, match="udev"):
        d.open()


def test_blit_validates_arguments(qapp):
    d = SimulatedDisplay()
    d.open()
    with pytest.raises(ValueError):
        d.blit(0, 0, 320, 10, bytes(321 * 11 * 2))  # x1 fora da tela
    with pytest.raises(ValueError):
        d.blit(0, 0, 1, 1, b"\x00")  # tamanho errado


def test_separate_changes_are_sent_as_separate_regions(qapp):
    d = SimulatedDisplay()
    d.open()
    sender = FrameSender(d)
    img = make_image()
    sender.send(img)
    paint_rect(img, (10, 10, 40, 10), "#ffffff")
    paint_rect(img, (250, 450, 40, 10), "#ff0000")
    report = sender.send(img)
    assert len(report.rects) == 2 and not report.full
    assert report.bytes_sent < 2 * 40 * 10 * 2 + 100  # só as duas regiões + cabeçalhos
    assert d.screen.to_qimage() == img.convertToFormat(QImage.Format.Format_RGB16)
    assert d.screen.errors == []
