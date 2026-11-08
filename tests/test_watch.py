"""Порог хода монеты, разговор с Олегом, отказы бирж, «источник перестал читаться», «рынок» одним сообщением."""
from __future__ import annotations

import asyncio
import math
import random

from news_watch import config as C
from news_watch.alerts import Decider
from news_watch.main import Health
from news_watch.net import Net
from news_watch.state import State
from news_watch.tg import ADD, LIST, REMOVE, Dialog
from news_watch.thresholds import ORANGE, RED, check, edges

NOW = 1_794_200_000.0


def _walk(n, sigma, seed=1, vol=1000.0):
    rnd = random.Random(seed)
    c, out = 100.0, []
    for _ in range(n):
        c *= math.exp(rnd.gauss(0, sigma))
        out.append(c)
    return out, [vol] * n


def test_edges_follow_the_coin_own_range():
    """Спокойная монета (σ 0.3 % на 15 мин) и бешеная (σ 3 %): край нормы у бешеной во много раз шире."""
    calm = edges(*_walk(2880, 0.003))
    wild = edges(*_walk(2880, 0.03, seed=2))
    assert calm[60].down >= -0.08 and wild[60].down < 3 * calm[60].down
    assert calm[15].down <= math.log(0.98)                      # не мельче 2 %
    assert all(e.up > 0 > e.down for e in calm.values()) and not calm[60].young


def test_young_coin_has_temporary_floor_and_too_short_is_none():
    e = edges(*_walk(200, 0.001))
    assert e[60].young and e[60].down <= math.log(1 - C.YOUNG_FLOOR) + 1e-12
    assert edges(*_walk(50, 0.01)) is None                      # меньше суток — «не посчитан», а не ноль


def _live(drop, minutes, vol_x):
    c = [100.0] * 241
    v = [1000.0 / 15] * 241                                     # обычный объём: 1000 за 15 мин
    for i in range(minutes):
        c[-minutes + i] = 100.0 * (1 + drop * (i + 1) / minutes)
        v[-minutes + i] = 1000.0 / 15 * vol_x
    return c, v


def test_move_levels_red_orange_and_quiet():
    e = edges(*_walk(2880, 0.004))
    d60 = math.exp(e[60].down) - 1                              # край нормы за час, напр. −3 %
    assert check(e, *_live(d60 * 1.7, 60, 1)).level == RED      # далеко за краем — 🔴 и без объёма
    m = check(e, *_live(d60 * 1.15, 60, 4))
    assert m.level == ORANGE and m.minutes == 60 and m.vol_x >= 3
    assert check(e, *_live(d60 * 1.15, 60, 1)) is None          # за краем, но объём обычный — молчим
    assert check(e, *_live(d60 * 0.5, 60, 10)) is None          # в норме — молчим


def test_market_wide_is_one_message(tmp_path):
    st = State(tmp_path, now=lambda: NOW)
    dec = Decider(st, now=lambda: NOW)
    e = edges(*_walk(2880, 0.004))
    mv = check(e, *_live(math.exp(e[60].down * 1.7) - 1, 60, 1))
    acts = dec.on_moves({t: (mv, "Binance") for t in ("ADA", "SOL", "XRP")})
    assert len(acts) == 1 and acts[0].text.startswith(f"{RED} рынок: резкий ход у 3 монет")
    assert dec.on_moves({"ADA": (mv, "Binance")}) == []          # пауза по монете
    one = Decider(State(tmp_path / "x", now=lambda: NOW), now=lambda: NOW).on_moves({"MX": (mv, "MEXC")})
    assert len(one) == 1 and one[0].text.startswith(f"{RED} MX — падение") and "(MEXC)" in one[0].text


class _St:
    def __init__(self):
        self.coins, self.saved = {}, 0

    def save_coins(self):
        self.saved += 1


def test_dialog_add_list_remove_and_strangers_ignored():
    st = _St()

    async def add(t):
        st.coins[t] = {"venue": "MEXC", "issuer": "MEXC"}
        return f"✅ {t} добавлена"

    d = Dialog(st, add)
    msg = lambda text, chat=42: {"message": {"chat": {"id": chat}, "text": text}}
    run = lambda u: asyncio.run(d.on_update(u, "42"))
    assert run(msg(ADD, chat=7)) == []                          # чужой чат — молчание
    assert run(msg(ADD))[0][0].startswith("Введи тикер")
    assert run(msg("mxusdt")) == [("✅ MX добавлена", None)]
    assert "MX — цена: MEXC, эмитент MEXC" in run(msg(LIST))[0][0]
    kb = run(msg(REMOVE))[0][1]
    assert kb["inline_keyboard"][0][0]["callback_data"] == "rm:MX"
    cb = {"callback_query": {"id": "1", "data": "rm:MX", "message": {"chat": {"id": 42}}}}
    assert run(cb) == [("✅ MX убрана", None)] and st.coins == {} and st.saved == 1
    assert run({"callback_query": {"id": "2", "data": "rm:ADA", "message": {"chat": {"id": 7}}}}) == []


class _Resp:
    def __init__(self, status, body=b"{}"):
        self.status, self.body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        return self.body


class _Session:
    def __init__(self, statuses):
        self.statuses, self.calls = list(statuses), []

    def get(self, url, params=None, headers=None):
        self.calls.append(url)
        return _Resp(self.statuses.pop(0))


def test_refusal_mutes_domain_for_30_minutes_and_bybit_is_spaced():
    t = [1000.0]
    slept = []

    async def sleep(s):
        slept.append(s)
        t[0] += s

    s = _Session([403, 200, 200, 200])
    net = Net(s, now=lambda: t[0], sleep=sleep)
    run = asyncio.run
    assert run(net.get("https://api.bitget.com/x")) == (None, "отказ 403")
    assert run(net.get("https://api.bitget.com/x")) == (None, "молчит после отказа") and len(s.calls) == 1
    t[0] += C.MUTE_ON_REFUSAL_S
    assert run(net.get("https://api.bitget.com/x"))[0] == b"{}"
    run(net.get("https://api.bybit.com/a"))
    run(net.get("https://api.bybit.com/b"))
    assert slept and slept[-1] >= 29.9                          # Bybit — не чаще раза в 30 с


def test_source_broken_reported_once_and_recovery_once():
    h = Health()
    assert h.result("FinCEN", False, "пусто при живом ответе") is None
    assert h.result("FinCEN", False, "пусто при живом ответе").startswith("🛠 FinCEN перестал читаться")
    assert h.result("FinCEN", False, "x") is None
    assert h.result("FinCEN", True) == "🛠 FinCEN снова читается"
    assert h.result("FinCEN", True) is None


def test_down_edge_is_the_coin_own_02_percent_tail():
    """Край нормы — 0.2 % худших часовых ходов самой монеты (когда нижняя граница 2 % / 3σ̂ не мешает)."""
    rnd = random.Random(5)
    c, cl = 100.0, []
    for _ in range(2880):                                        # тяжёлые хвосты: t-распределение
        c *= math.exp(0.01 * rnd.gauss(0, 1) / max(0.2, abs(rnd.gauss(0, 1))) ** 0.5)
        cl.append(c)
    e = edges(cl, [1.0] * len(cl))[60]
    r = [math.log(cl[i] / cl[i - 4]) for i in range(4, len(cl))]
    share = sum(x <= e.down for x in r) / len(r)
    assert 0.001 <= share <= 0.003, share


def test_history_pages_with_both_bounds_like_mexc_needs():
    """MEXC без endTime отдаёт последние 500 свечей (08.11) — каждое окно задаётся с обеих сторон, 30 дней целиком."""
    import json as _j
    from news_watch import prices as P
    now_ms = 1_794_200_000_000
    asked = []

    class _N:
        async def get(self, url, params=None):
            asked.append(params)
            if "endTime" not in params:                       # как MEXC: без конца окна — последние 500
                s = now_ms - 500 * 900_000
            else:
                s = params["startTime"]
            rows = [[s + i * 900_000, "1", "1", "1", "1", "1", 0, "10"] for i in range(500)
                    if s + i * 900_000 <= min(params.get("endTime", now_ms), now_ms)]
            return _j.dumps(rows).encode(), None

    cl, vo = asyncio.run(P.history15(_N(), "MEXC", "MXUSDT", now_ms=now_ms))
    assert len(cl) >= 30 * 96 - 2 and all(p.get("endTime") for p in asked)
