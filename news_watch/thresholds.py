"""Порог хода — свой у каждой монеты, из её 30 дней 15-минуток (план, п. 3).

Для окна h (15 мин, 1 ч, 4 ч = 1, 4, 16 баров):
  r_h = ln(C_t / C_{t−h}) по всем сдвигам истории;
  D_h — квантиль 0.2 % (вниз), U_h — 99.8 % (вверх), σ̂_h = 1.4826 · медиана |r − медиана r|;
  края не мельче max(2 %, 3σ̂) в лог-ходах; у молодой монеты (< 7 дней) — не мельче 8 % (окно ≥ 1 ч);
  V_h — медиана объёма за окно.
Тревога по живым минутным ценам: 🔴 — r ≤ 1.5·D или ниже 30-дневного минимума хода; 🟠 — r ≤ D и объём ≥ 3·V;
вверх — 🟠 «рост» при r ≥ U и объёме ≥ 3·V.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from . import config as C

RED, ORANGE = "🔴", "🟠"


@dataclass(frozen=True)
class Edge:
    minutes: int
    down: float          # D_h (< 0), лог-ход
    up: float            # U_h (> 0)
    worst: float         # 30-дневный минимум хода
    vol: float           # медиана объёма окна
    young: bool


def _quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    i = q * (len(s) - 1)
    lo, hi = math.floor(i), math.ceil(i)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def edges(closes15: list[float], vols15: list[float]) -> dict[int, Edge] | None:
    """Пороги по окнам из истории 15-минуток. Истории меньше суток — None («порог не считается», а не ноль)."""
    if len(closes15) < 96 or len(closes15) != len(vols15) or min(closes15) <= 0:
        return None
    young = len(closes15) < C.YOUNG_BARS
    out = {}
    for minutes in C.WINDOWS_MIN:
        h = minutes // 15
        r = [math.log(closes15[i] / closes15[i - h]) for i in range(h, len(closes15))]
        v = [sum(vols15[i - h + 1:i + 1]) for i in range(h - 1, len(vols15))]
        med = statistics.median(r)
        sigma = 1.4826 * statistics.median(abs(x - med) for x in r)
        # Не мельче 2 % простого хода в каждую сторону (вниз −2 % = ln 0.98) и 3σ̂.
        floor_dn = max(-math.log(1 - C.FLOOR_MOVE), C.FLOOR_SIGMAS * sigma)
        floor_up = max(math.log(1 + C.FLOOR_MOVE), C.FLOOR_SIGMAS * sigma)
        if young and minutes >= 60:
            floor_dn = max(floor_dn, -math.log(1 - C.YOUNG_FLOOR))
            floor_up = max(floor_up, math.log(1 + C.YOUNG_FLOOR))
        down = min(_quantile(r, C.TAIL_Q), -floor_dn)
        up = max(_quantile(r, 1 - C.TAIL_Q), floor_up)
        out[minutes] = Edge(minutes, down, up, min(r), statistics.median(v), young)
    return out


@dataclass(frozen=True)
class Move:
    level: str           # 🔴 / 🟠
    minutes: int
    pct: float           # ход в процентах (простых)
    norm_pct: float      # край нормы монеты в процентах
    vol_x: float | None  # объём к обычному
    up: bool


def check(edge_map: dict[int, Edge], closes1: list[float], vols1: list[float]) -> Move | None:
    """Самая тяжёлая тревога по окнам на живых минутных ценах (последняя — текущая)."""
    best = None
    for minutes, e in edge_map.items():
        if len(closes1) <= minutes or closes1[-1 - minutes] <= 0 or closes1[-1] <= 0:
            continue
        r = math.log(closes1[-1] / closes1[-1 - minutes])
        vol = sum(vols1[-minutes:])
        vx = vol / e.vol if e.vol > 0 else None
        big_vol = vx is not None and vx >= C.VOLUME_X
        level = None
        if r <= C.RED_FACTOR * e.down or r < e.worst:
            level = RED
        elif r <= e.down and big_vol:
            level = ORANGE
        elif r >= e.up and big_vol:
            level = ORANGE
        if level is None:
            continue
        up = r > 0
        m = Move(level, minutes, (math.exp(r) - 1) * 100, (math.exp(e.up if up else e.down) - 1) * 100, vx, up)
        if best is None or (m.level == RED and best.level != RED) or (m.level == best.level and abs(m.pct) > abs(best.pct)):
            best = m
    return best
