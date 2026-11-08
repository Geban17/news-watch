"""Новостной сторож: цикл раз в минуту + разговор с Олегом. Запуск: python -m news_watch.main"""
from __future__ import annotations

import asyncio
import logging
import time

from . import config as C
from . import prices as P
from .alerts import Action, Decider
from .forward import ForwardLog
from .net import Net
from .sources import ParseError, parse
from .state import State
from .tg import Bot, Dialog, updates_offset
from .thresholds import edges as make_edges, check

log = logging.getLogger("news_watch")


class Health:
    """«Источник перестал читаться» — одна строка при поломке и одна при починке (правило 21: не смог ≠ пусто)."""

    def __init__(self):
        self.bad: dict[str, int] = {}
        self.reported: set[str] = set()

    def result(self, name: str, ok: bool, why: str = "") -> str | None:
        if ok:
            self.bad.pop(name, None)
            if name in self.reported:
                self.reported.discard(name)
                return f"🛠 {name} снова читается"
            return None
        self.bad[name] = self.bad.get(name, 0) + 1
        if self.bad[name] >= C.BROKEN_AFTER and name not in self.reported:
            self.reported.add(name)
            return f"🛠 {name} перестал читаться ({why}) — остальные источники работают"
        return None


class Watch:
    def __init__(self, net: Net, bot: Bot, state: State, now=time.time):
        self.net, self.bot, self.st, self.now = net, bot, state, now
        self.dec = Decider(state, now)
        self.health = Health()
        self.next_due: dict[str, float] = {}
        self.edges: dict[str, dict] = {}
        self.edges_ts: dict[str, float] = {}
        self.forward = ForwardLog(state.folder / "exchange_events.json", now)

    async def add_coin(self, t: str) -> str:
        venue = await P.pick_venue(self.net, t)
        issuer = C.ISSUERS.get(t, (None,))[0]
        self.st.coins[t] = {"venue": venue[0] if venue else None, "symbol": venue[1] if venue else None,
                            "issuer": issuer}
        self.st.save_coins()
        parts = [f"✅ {t} добавлена"]
        parts.append(f"цена: {venue[0]}" if venue else "цена не отслеживается (нет на Binance и MEXC)")
        if issuer:
            parts.append(f"эмитент: {issuer}")
        if venue:
            await self.refresh_edges(t)
            e = self.edges.get(t, {}).get(60)
            parts.append(f"норма за 1 ч — до {(2.718281828 ** e.down - 1) * 100:+.1f} %" if e
                         else "порог хода не посчитан (истории нет)")
            if e and e.young:
                parts.append("порог временный: истории меньше 7 дней")
        return " · ".join(parts)

    async def refresh_edges(self, t: str) -> None:
        c = self.st.coins.get(t) or {}
        if not c.get("venue"):
            return
        h = await P.history15(self.net, c["venue"], c["symbol"])
        self.edges_ts[t] = self.now()
        e = make_edges(*h) if h else None
        if e:
            self.edges[t] = e
        else:
            self.edges.pop(t, None)

    async def do(self, actions: list[Action]) -> None:
        for a in actions:
            try:
                if a.kind == "send":
                    mid = await self.bot.send(a.text)
                    if a.event:
                        self.dec.bound(a.event, mid)
                else:
                    await self.bot.edit(a.message_id, a.text)
            except Exception as e:
                log.warning("telegram: %s", e)

    async def sources_round(self) -> None:
        now = self.now()
        for src in C.SOURCES:
            name, _kind, _parser, url, period, _w = src
            if self.next_due.get(name, 0) > now:
                continue
            self.next_due[name] = now + period
            raw, err = await self.net.get(url)
            if raw is None:
                note = self.health.result(name, False, err)
            else:
                try:
                    items = parse(src, raw)
                except (ParseError, ValueError, KeyError, TypeError) as e:
                    items, err = None, f"не разобрался: {type(e).__name__}"
                if items:
                    note = self.health.result(name, True)
                    await self.do(self.dec.on_items(name, items))
                else:
                    note = self.health.result(name, False, err or "пусто при живом ответе")
            if note:
                await self.do([Action("send", note)])

    async def prices_round(self) -> None:
        now = self.now()
        moves = {}
        for t, c in sorted(self.st.coins.items()):
            if not c.get("venue"):
                continue
            if t not in self.edges and now - self.edges_ts.get(t, 0) >= 3600 or now - self.edges_ts.get(t, 0) >= 86400:
                await self.refresh_edges(t)
            if t not in self.edges:
                continue
            live = await P.live1m(self.net, c["venue"], c["symbol"])
            if live is None:
                continue
            m = check(self.edges[t], *live)
            if m:
                moves[t] = (m, c["venue"])
        if moves:
            await self.do(self.dec.on_moves(moves))

    async def forward_round(self) -> None:
        """🔴 о бирже → строка в файл проверки вперёд; дописать цены через 1 / 3 / 7 дней."""
        while self.dec.exchange_events:
            ev = self.dec.exchange_events.pop(0)
            self.forward.add(ev, await P.last_price(self.net, ev["token"]), await P.last_price(self.net, "BTC"))
        for row, field in self.forward.due():
            self.forward.fill(row, field, await P.last_price(self.net, row["token"]), await P.last_price(self.net, "BTC"))

    async def round(self) -> None:
        if C.quiet_minute(time.gmtime(self.now()).tm_min):
            return
        await self.sources_round()
        await self.forward_round()
        await self.prices_round()
        self.st.save_sent()


async def tg_loop(bot: Bot, dialog: Dialog) -> None:
    offset = 0
    while True:
        try:
            ups = await bot.call("getUpdates", offset=offset, timeout=25, allowed_updates=["message", "callback_query"])
        except Exception as e:
            log.warning("getUpdates: %s", e)
            await asyncio.sleep(10)
            continue
        offset = updates_offset(ups, offset)
        for u in ups:
            try:
                for text, markup in await dialog.on_update(u, bot.chat_id):
                    await bot.send(text, markup)
                if u.get("callback_query"):
                    await bot.call("answerCallbackQuery", callback_query_id=u["callback_query"]["id"])
            except Exception as e:
                log.warning("диалог: %s", e)


async def watch_loop(w: Watch) -> None:
    while True:
        try:
            await w.round()
        except Exception as e:
            log.warning("оборот: %s", e)
        await asyncio.sleep(60 - time.time() % 60 + 5)


async def amain() -> None:
    import aiohttp
    if not C.TG_TOKEN or not C.TG_CHAT_ID:
        raise SystemExit("нет NEWS_TG_TOKEN / NEWS_TG_CHAT_ID — bin/set_token")
    st = State()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        net, bot = Net(s), Bot(s, C.TG_TOKEN, C.TG_CHAT_ID)
        w = Watch(net, bot, st)
        await asyncio.gather(tg_loop(bot, Dialog(st, w.add_coin)), watch_loop(w))


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(amain())
