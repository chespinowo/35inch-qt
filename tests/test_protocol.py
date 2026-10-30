import random

import pytest

from usbmonitor_qt.protocol import (
    Command,
    Orientation,
    decode_command,
    encode_command,
    hello_packet,
    level_to_raw_brightness,
    orientation_packet,
    oriented_size,
)


def test_known_vector_full_screen_bitmap():
    # (0,0)-(319,479) em DISPLAY_BITMAP: valores conferidos à mão contra o empacotamento de bits
    assert encode_command(Command.DISPLAY_BITMAP, 0, 0, 319, 479) == bytes([0x00, 0x00, 0x04, 0xFD, 0xDF, 0xC5])


def test_encode_decode_roundtrip():
    rng = random.Random(1)
    for _ in range(500):
        vals = [rng.randint(0, 1023) for _ in range(4)]
        cmd = rng.choice(list(Command))
        assert decode_command(encode_command(cmd, *vals)) == (int(cmd), *vals)


@pytest.mark.parametrize("bad", [-1, 1024, 5000])
def test_coordinate_range_is_checked(bad):
    with pytest.raises(ValueError):
        encode_command(Command.DISPLAY_BITMAP, bad, 0, 0, 0)


def test_hello_packet():
    assert hello_packet() == bytes([69]) * 6


def test_brightness_scale():
    assert level_to_raw_brightness(100) == 0  # mais claro
    assert level_to_raw_brightness(0) == 255  # mais escuro
    assert level_to_raw_brightness(100, invert=True) == 255
    assert level_to_raw_brightness(0, invert=True) == 0
    assert level_to_raw_brightness(150) == 0 and level_to_raw_brightness(-5) == 255


def test_orientation_packet_layout():
    pkt = orientation_packet(Orientation.LANDSCAPE, 480, 320)
    assert len(pkt) == 16
    assert pkt[5] == Command.SET_ORIENTATION
    assert pkt[6] == 102
    assert int.from_bytes(pkt[7:9], "big") == 480
    assert int.from_bytes(pkt[9:11], "big") == 320
    assert pkt[11:] == bytes(5)


def test_oriented_size():
    assert oriented_size(Orientation.PORTRAIT) == (320, 480)
    assert oriented_size(Orientation.REVERSE_PORTRAIT) == (320, 480)
    assert oriented_size(Orientation.LANDSCAPE) == (480, 320)
    assert oriented_size(Orientation.REVERSE_LANDSCAPE) == (480, 320)
