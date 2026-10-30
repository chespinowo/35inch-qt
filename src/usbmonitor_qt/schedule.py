"""Lógica de horário e de rotação de cenas (pura, sem Qt, fácil de testar)."""

from __future__ import annotations

from datetime import time


def parse_hhmm(text: str, default: time) -> time:
    """'22:30' -> time(22, 30). Texto inválido devolve `default`."""
    try:
        hours, minutes = str(text).split(":")
        return time(int(hours), int(minutes))
    except (ValueError, TypeError):
        return default


def night_active(now: time, start: time, end: time) -> bool:
    """True se `now` está dentro da janela [start, end).

    A janela pode atravessar a meia-noite (22:00 → 07:00). Início igual ao fim
    significa janela vazia, não "o dia todo".
    """
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


def carousel_pick(indexes: list[int], elapsed: float, interval: float, fallback: int) -> int:
    """Qual cena mostrar na rotação, `elapsed` segundos após o início.

    Com menos de duas cenas selecionadas não há o que rodar: devolve `fallback`.
    """
    if len(indexes) < 2:
        return fallback
    step = int(max(0.0, elapsed) // max(1.0, interval))
    return indexes[step % len(indexes)]
