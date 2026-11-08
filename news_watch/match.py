"""Какие монеты Олега задевает новость и насколько она плохая / хорошая (план, п. 4–5). Без ИИ — словарь."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import config as C
from .sources import Item

# Тикеры, совпадающие с обычными словами: в СМИ ищем их только по имени монеты или эмитента.
SHORT_OR_WORD = 3


@dataclass(frozen=True)
class Coin:
    ticker: str
    name: str | None = None      # «MX Token» — для СМИ
    issuer: str | None = None    # «MEXC» — новости о бирже = новости о монете


def _word(text: str, needle: str, case: bool) -> bool:
    flags = 0 if case else re.IGNORECASE
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])", text, flags) is not None


def mentions(item: Item, coin: Coin) -> str | None:
    """Почему новость про монету: «тикер», «имя», «эмитент MEXC» — или None."""
    t, title = coin.ticker, item.title
    if item.kind == "media":
        if f"({t})" in title or (len(t) > SHORT_OR_WORD and _word(title, t, case=True)):
            return "тикер"
    elif _word(title, t, case=True) or f"{t}USDT" in title or f"{t}/USDT" in title or f"{t}-USDT" in title:
        return "тикер"
    if coin.name and _word(title, coin.name, case=False):
        return "имя"
    issuer = C.ISSUERS.get(t)
    if issuer and any(_word(title, alias, case=False) for alias in issuer[1]):
        return f"эмитент {issuer[0]}"
    return None


def _hits(title: str, patterns: list[str]) -> bool:
    return any(re.search(p, title, re.IGNORECASE) for p in patterns)


def severity(title: str) -> float:
    """Тяжесть плохих слов: 1.0 / 0.7 / 0.4 / 0 (нет плохих)."""
    if _hits(title, C.SEVERE):
        return 1.0
    if _hits(title, C.SERIOUS):
        return 0.7
    if _hits(title, C.MINOR):
        return 0.4
    return 0.0


def big_listing(item: Item, why: str | None) -> bool:
    """🟢 — только официальный листинг самой монеты на крупной бирже и без плохих слов."""
    return (item.kind == "exchange" and item.source in C.BIG_LISTING_EXCHANGES and why == "тикер"
            and severity(item.title) == 0.0 and _hits(item.title, C.GOOD))
