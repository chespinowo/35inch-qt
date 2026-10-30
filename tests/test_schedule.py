from datetime import time

import pytest

from usbmonitor_qt.schedule import carousel_pick, night_active, parse_hhmm


@pytest.mark.parametrize(
    "now, expected",
    [
        (time(22, 0), True),  # início é inclusivo
        (time(23, 59), True),
        (time(0, 0), True),  # atravessou a meia-noite
        (time(3, 30), True),
        (time(6, 59), True),
        (time(7, 0), False),  # fim é exclusivo
        (time(12, 0), False),
        (time(21, 59), False),
    ],
)
def test_night_window_crossing_midnight(now, expected):
    assert night_active(now, time(22, 0), time(7, 0)) is expected


def test_night_window_same_day():
    assert night_active(time(14, 0), time(13, 0), time(15, 0))
    assert not night_active(time(15, 0), time(13, 0), time(15, 0))
    assert not night_active(time(12, 59), time(13, 0), time(15, 0))


def test_equal_start_and_end_is_an_empty_window_not_all_day():
    assert not night_active(time(10, 0), time(8, 0), time(8, 0))
    assert not night_active(time(8, 0), time(8, 0), time(8, 0))


@pytest.mark.parametrize("text", ["25:99", "abc", "", "12", None, "12:xx"])
def test_parse_hhmm_invalid_returns_default(text):
    assert parse_hhmm(text, time(22, 0)) == time(22, 0)


def test_parse_hhmm_valid():
    assert parse_hhmm("07:05", time(0, 0)) == time(7, 5)
    assert parse_hhmm("23:59", time(0, 0)) == time(23, 59)


def test_carousel_needs_two_scenes_otherwise_falls_back():
    assert carousel_pick([], 100, 10, fallback=3) == 3
    assert carousel_pick([2], 100, 10, fallback=3) == 3


def test_carousel_steps_and_wraps():
    picked = [0, 1, 4]
    assert [carousel_pick(picked, t, 10, 0) for t in (0, 9.9, 10, 19.9, 20, 29.9, 30, 45)] == [0, 0, 1, 1, 4, 4, 0, 1]


def test_carousel_edge_values():
    assert carousel_pick([0, 1], -5, 10, 0) == 0  # tempo negativo não quebra
    assert carousel_pick([0, 1], 3, 0, 0) == 1  # intervalo 0 vira 1 s, em vez de dividir por zero
