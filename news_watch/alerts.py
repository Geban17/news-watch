"""Решение «слать / дописать / молчать» (план, п. 4).

счёт = вес источника × тяжесть слов × подтверждение ценой (× 1.5, если у монеты ход за краем нормы в те же ±60 мин).
  🔴 — счёт ≥ 1.0 (официальная биржа или госорган с тяжёлыми словами) или ход 🔴;
  🟠 — счёт ≥ 0.4 и плохое из ≥ 2 независимых источников, или ход 🟠;
  🟢 — только официальный листинг монеты на Binance / Bybit / OKX / Coinbase.
Одно событие (монета, плохое / хорошее, сутки UTC) — одно сообщение; подтверждения дописываются в него.
По монете — не чаще раза в 6 ч, кроме ухудшения. Первый обход источника — всё «видено» без отправки.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from . import config as C
from .match import Coin, big_listing, mentions, severity
from .sources import Item
from .state import State
from .thresholds import Move, ORANGE, RED

GREEN = "🟢"
RANK = {GREEN: 0, ORANGE: 1, RED: 2}
PRICE_CONFIRM_S = 3600
PRICE_CONFIRM_X = 1.5
WINDOW_NAME = {15: "15 мин", 60: "1 ч", 240: "4 ч"}
# 🔴 о самой бирже — строка про её токен (координатор 2123, «да» Олега 21:22; разбор 512: 6 из 6, мало данных).
TOKEN_LINE = "🟠 токен биржи {t} после таких новостей исторически падал ещё 1–3 дня (512: 6 из 6, мало данных)"


@dataclass
class Action:
    kind: str               # send | edit
    text: str
    event: str | None = None
    message_id: int | None = None


def _trouble(title: str) -> bool:
    return any(re.search(p, title, re.IGNORECASE) for p in C.EXCHANGE_TROUBLE)


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _same(it) -> str | None:
    """Ключ «та же новость у того же источника»: лента и её запасная страница («SEC» / «SEC · страница») — один ключ по
    ссылке. Только у источников с запасным адресом (C.FALLBACKS): у прочих ссылки не сверяются."""
    base = it.source.split(" · ")[0]
    return f"url|{base}|{it.url}" if it.url and base in C.FALLBACKS else None


class Decider:
    def __init__(self, state: State, now=time.time):
        self.st, self.now = state, now
        self.exchange_events: list[dict] = []   # 🔴 о бирже — для файла проверки вперёд (main дописывает цены)

    # ---- монеты ----------------------------------------------------------
    def coins(self) -> list[Coin]:
        return [Coin(t, c.get("name"), C.ISSUERS.get(t, (None,))[0]) for t, c in sorted(self.st.coins.items())]

    def _allowed(self, ticker: str, level: str) -> bool:
        """Пауза 6 ч по монете — кроме ухудшения."""
        last = self.st.coin_last.get(ticker)
        if not last or "level" not in last:
            return True
        return self.now() - last["ts"] >= C.COIN_COOLDOWN_S or RANK[level] > RANK[last["level"]]

    def _mark_coin(self, ticker: str, level: str) -> None:
        rec = self.st.coin_last.setdefault(ticker, {})
        rec.update(ts=self.now(), level=level)

    # ---- новости ---------------------------------------------------------
    def on_items(self, source: str, items: list[Item]) -> list[Action]:
        now = self.now()
        if source not in self.st.primed:
            # Первый обход: всё, что уже лежит в ленте, — «видено» без отправки (иначе придёт архив за месяц).
            for it in items:
                self.st.items[it.key] = now
                if _same(it):
                    self.st.items[_same(it)] = now
            self.st.primed[source] = now
            return []
        actions = []
        for it in items:
            if it.key in self.st.items or (_same(it) and _same(it) in self.st.items):
                continue                                  # та же новость с другого адреса источника (лента / страница) — один раз
            self.st.items[it.key] = now
            if _same(it):
                self.st.items[_same(it)] = now
            listed = self.coins()
            # Токены бирж, которых нет в списке Олега: только 🔴 о самой бирже (координатор 2123).
            extra = [Coin(t, None, C.ISSUERS[t][0]) for t in sorted(C.ISSUER_TOKENS) if t not in self.st.coins]
            for coin in listed + extra:
                why = mentions(it, coin)
                if why:
                    a = self._news(it, coin, why, red_only=coin in extra)
                    if a:
                        actions.append(a)
        return actions

    def _news(self, it: Item, coin: Coin, why: str, red_only: bool = False) -> Action | None:
        now, t = self.now(), coin.ticker
        if why.startswith("эмитент") and (it.source == C.ISSUERS[t][0] or not _trouble(it.title)):
            return None                                   # о бирже — только её беда и только из чужих источников
        if big_listing(it, why):
            cls, level = "good", GREEN
        else:
            sev = severity(it.title)
            if sev == 0:
                return None
            moved = self.st.coin_last.get(t, {}).get("move_ts", 0)
            confirm = PRICE_CONFIRM_X if now - moved <= PRICE_CONFIRM_S else 1.0
            score = it.weight * sev * confirm
            cls, level = "bad", None
        key = f"{t}|{cls}|{_day(now)}"
        ev = self.st.events.setdefault(key, {"ts": now, "sources": [], "level": None, "msg": None, "why": why})
        if any(s["source"] == it.source for s in ev["sources"]):
            return None                                   # тот же источник второй раз — не подтверждение
        ev["sources"].append({"source": it.source, "title": it.title, "url": it.url})
        if cls == "bad":
            independent = len(ev["sources"])
            score = ev["score"] = max(ev.get("score", 0.0), score)   # счёт события — лучший из его источников
            if score >= 1.0:
                level = RED
            elif score >= 0.4 and independent >= 2:
                level = ORANGE
        if red_only and level != RED and ev["msg"] is None:
            return None
        if ev["msg"] is not None:
            if level and RANK[level] > RANK[ev["level"]]:
                ev["level"] = level                       # ухудшение — новым сообщением
                ev["msg"] = None
                self._mark_coin(t, level)
                return Action("send", self._text(t, ev), event=key)
            return Action("edit", self._text(t, ev), event=key, message_id=ev["msg"])
        if level is None or not self._allowed(t, level):
            return None
        ev["level"] = level
        self._mark_coin(t, level)
        if level == RED and why.startswith("эмитент"):
            self.exchange_events.append({"exchange": C.ISSUERS[t][0], "token": t, "source": it.source,
                                         "title": it.title, "url": it.url})
        return Action("send", self._text(t, ev), event=key)

    def _text(self, t: str, ev: dict) -> str:
        first = ev["sources"][0]
        why = "" if ev["why"] == "тикер" else f" ({ev['why']})"
        lines = [f"{ev['level']} {t}{why} — {first['source']}: {first['title']}", first["url"]]
        if ev["level"] == RED and ev["why"].startswith("эмитент"):
            lines.append(TOKEN_LINE.format(t=t))
        more = [s["source"] for s in ev["sources"][1:]]
        if more:
            lines.append("ещё: " + ", ".join(more))
        return "\n".join(x for x in lines if x)

    def bound(self, event: str, message_id: int) -> None:
        self.st.events[event]["msg"] = message_id

    # ---- ход цены --------------------------------------------------------
    def on_moves(self, moves: dict[str, tuple[Move, str]]) -> list[Action]:
        """moves: тикер → (тревога, биржа цены). ≥ 3 монет разом — одно сообщение «рынок»."""
        now = self.now()
        for t in moves:
            self.st.coin_last.setdefault(t, {})["move_ts"] = now
        ok = {t: mv for t, mv in moves.items() if self._allowed(t, mv[0].level)}
        if not ok:
            return []
        if len(ok) >= C.MARKET_WIDE_COINS:
            level = RED if any(m.level == RED for m, _ in ok.values()) else ORANGE
            parts = [f"{t} {m.pct:+.1f} %" for t, (m, _) in sorted(ok.items(), key=lambda kv: kv[1][0].pct)]
            for t in ok:
                self._mark_coin(t, level)
            return [Action("send", f"{level} рынок: резкий ход у {len(ok)} монет — " + ", ".join(parts))]
        out = []
        for t, (m, venue) in sorted(ok.items()):
            self._mark_coin(t, m.level)
            norm = f"норма {t} за {WINDOW_NAME[m.minutes]} — до {m.norm_pct:+.1f} %"
            vol = f", объём ×{m.vol_x:.0f} к обычному" if m.vol_x else ""
            what = "рост" if m.up else "падение"
            out.append(Action("send", f"{m.level} {t} — {what} {m.pct:+.1f} % за {WINDOW_NAME[m.minutes]} ({venue})"
                                      f"{vol}; {norm}"))
        return out
