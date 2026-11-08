"""Цены и объёмы — только в памяти (план, п. 2–3). Биржа цены — Binance или MEXC (у обеих один формат свечей).

Bybit не трогаем вовсе (общий адрес с боем 2ч). Монеты, которых нет ни на Binance, ни на MEXC, — «цена не
отслеживается» словами в карточке, а не тихий ноль.
"""
from __future__ import annotations

import json
import time

from . import config as C

VENUES = {"Binance": "https://api.binance.com", "MEXC": "https://api.mexc.com"}


def parse_klines(raw: bytes) -> tuple[list[int], list[float], list[float]]:
    """Свечи v3 (Binance / MEXC): время открытия, закрытие, объём в USDT (поле 7)."""
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError(f"не свечи: {str(rows)[:100]}")
    return [int(r[0]) for r in rows], [float(r[4]) for r in rows], [float(r[7]) for r in rows]


def parse_quote_volume(raw: bytes) -> float | None:
    d = json.loads(raw)
    try:
        return float(d["quoteVolume"])
    except (KeyError, TypeError, ValueError):
        return None


async def pick_venue(net, ticker: str) -> tuple[str, str] | None:
    """Где у монеты больше оборот за сутки: (биржа, символ) или None."""
    best = None
    for venue, base in VENUES.items():
        sym = f"{ticker}USDT"
        raw, _err = await net.get(f"{base}/api/v3/ticker/24hr", {"symbol": sym})
        qv = parse_quote_volume(raw) if raw else None
        if qv and (best is None or qv > best[2]):
            best = (venue, sym, qv)
    return (best[0], best[1]) if best else None


async def history15(net, venue: str, symbol: str, now_ms: int | None = None):
    """30 дней 15-минуток: три страницы по 1000. Сбой — None (порог «не посчитан»), не пустая история."""
    now_ms = now_ms or int(time.time() * 1000)
    start = now_ms - C.HIST_DAYS * 86_400_000
    ts, cl, vo = [], [], []
    cursor = start
    for _ in range(4):
        raw, err = await net.get(f"{VENUES[venue]}/api/v3/klines",
                                 {"symbol": symbol, "interval": "15m", "startTime": cursor, "limit": 1000})
        if raw is None:
            return None
        t, c, v = parse_klines(raw)
        if not t:
            break
        ts += t; cl += c; vo += v
        cursor = t[-1] + 900_000
        if cursor >= now_ms or len(t) < 1000:
            break
    # Последняя свеча ещё идёт — отрезаем.
    while ts and ts[-1] + 900_000 > now_ms:
        ts.pop(); cl.pop(); vo.pop()
    return cl, vo


async def live1m(net, venue: str, symbol: str):
    raw, err = await net.get(f"{VENUES[venue]}/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 241})
    if raw is None:
        return None
    _t, c, v = parse_klines(raw)
    return c, v


async def last_price(net, ticker: str) -> float | None:
    """Цена токена в USDT: Binance, иначе MEXC. Нет нигде или сбой — None (не ноль)."""
    for venue, base in VENUES.items():
        raw, _err = await net.get(f"{base}/api/v3/ticker/price", {"symbol": f"{ticker}USDT"})
        if raw:
            try:
                return float(json.loads(raw)["price"])
            except (KeyError, TypeError, ValueError):
                continue
    return None
