"""Источники, словарь, уровни, дубли, приёмка FinCEN → MEXC (06.11.2026), «уже прислал» на 7 суток."""
from __future__ import annotations

import pytest

from news_watch import config as C
from news_watch.alerts import Decider, GREEN
from news_watch.sources import Item, ParseError, parse
from news_watch.state import State
from news_watch.thresholds import RED, ORANGE

from conftest import FIX, src

NOW = 1_794_200_000.0   # 08.11.2026


def _state(tmp_path, coins, primed=True):
    st = State(tmp_path, now=lambda: NOW)
    st.coins = {t: {"venue": None, "symbol": None, "issuer": C.ISSUERS.get(t, (None,))[0]} for t in coins}
    if primed:
        st.primed = {s[0]: 0 for s in C.SOURCES + [C.BINANCE_CMS]}
    return st


@pytest.mark.parametrize("name,fixture,min_rows", [
    ("Bybit", "bybit_ann.json", 10), ("OKX", "okx_ann.json", 10), ("Bitget", "bitget_ann.json", 5),
    ("KuCoin", "kucoin_ann.json", 10), ("Binance", "binance_cms.json", 50), ("SEC", "sec_press.rss", 10),
    ("DOJ", "doj_news.rss", 10), ("FinCEN", "fincen_news.html", 5), ("OFAC", "ofac_recent.html", 5),
    ("CFTC", "cftc_press.html", 5), ("CoinDesk", "coindesk.rss", 10), ("The Block", "theblock.rss", 10),
    ("Cointelegraph", "cointelegraph.rss", 10), ("Decrypt", "decrypt.rss", 10), ("CryptoSlate", "cryptoslate.rss", 5),
    ("Bitcoin Magazine", "bitcoinmagazine.rss", 5), ("Bitcoinist", "bitcoinist.rss", 5)])
def test_every_source_snapshot_parses(name, fixture, min_rows):
    items = parse(src(name), (FIX / fixture).read_bytes())
    assert len(items) >= min_rows
    assert all(i.title and i.url.startswith("http") for i in items)
    assert len({i.key for i in items}) == len(items)                 # ключи различны — «уже прислал» не склеит разное


def test_exchange_error_is_an_error_not_silence():
    with pytest.raises(ParseError):
        parse(src("Bybit"), b'{"retCode": 10006, "retMsg": "Too many visits"}')
    with pytest.raises(ParseError):
        parse(src("SEC"), b"<html>maintenance")
    with pytest.raises(ParseError):
        parse(src("SEC"), b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x">]><rss/>')


def _feed(dec, name, fixture):
    return dec.on_items(name, parse(src(name), (FIX / fixture).read_bytes()))


def test_fincen_against_mexc_is_one_red_for_mx_and_media_confirm_it(tmp_path):
    """Приёмка (Олег, 2027): FinCEN 06.11.2026 «Operation Economic Outcast Severs MEXC Global's Access …» — 🔴 MX
    через эмитента; пять СМИ в тот же день — дописаны в то же сообщение, новыми не приходят."""
    st = _state(tmp_path, ["MX", "ADA"])
    dec = Decider(st, now=lambda: NOW)
    acts = _feed(dec, "FinCEN", "fincen_news.html")
    assert [a.kind for a in acts] == ["send"]
    first = acts[0].text.splitlines()[0]
    assert first.startswith(f"{RED} MX (эмитент MEXC) — FinCEN: ") and "MEXC" in first
    dec.bound(acts[0].event, 101)
    media = [("CoinDesk", "coindesk.rss"), ("The Block", "theblock.rss"), ("Cointelegraph", "cointelegraph.rss"),
             ("Decrypt", "decrypt.rss"), ("Bitcoin Magazine", "bitcoinmagazine.rss")]
    edits = []
    for name, fx in media:
        a = _feed(dec, name, fx)
        assert all(x.kind == "edit" and x.message_id == 101 for x in a), (name, a)
        edits += a
    assert len(edits) == 5
    assert edits[-1].text.splitlines()[-1] == "ещё: " + ", ".join(n for n, _ in media)
    # Второй обход тех же лент — ничего.
    for name, fx in [("FinCEN", "fincen_news.html")] + media:
        assert _feed(dec, name, fx) == []


def test_first_round_of_a_source_is_only_marked_seen(tmp_path):
    st = _state(tmp_path, ["MX"], primed=False)
    dec = Decider(st, now=lambda: NOW)
    assert _feed(dec, "FinCEN", "fincen_news.html") == []
    assert "FinCEN" in st.primed and len(st.items) >= 5


def _item(source, title, weight, kind="media"):
    return Item(source, kind, f"{source}|{title}", title, "https://x/" + source, None, weight)


def test_orange_needs_two_independent_bad_sources(tmp_path):
    st = _state(tmp_path, ["ADA"])
    dec = Decider(st, now=lambda: NOW)
    assert dec.on_items("CoinDesk", [_item("CoinDesk", "Cardano (ADA) foundation hacked", 0.6)]) == []   # 0.6, один
    acts = dec.on_items("Decrypt", [_item("Decrypt", "ADA (ADA) exploit drained wallets", 0.4)])
    assert [a.kind for a in acts] == ["send"] and acts[0].text.startswith(f"{ORANGE} ADA")
    # Слабое слово из одного СМИ: 0.4 × 0.7 = 0.28 < 0.4 — молчим.
    st2 = _state(tmp_path / "b", ["SOL"])
    d2 = Decider(st2, now=lambda: NOW)
    assert d2.on_items("Decrypt", [_item("Decrypt", "Solana (SOL) faces lawsuit", 0.4)]) == []
    assert d2.on_items("Bitcoinist", [_item("Bitcoinist", "Solana (SOL) lawsuit widens", 0.4)]) == []


def test_coin_pause_six_hours_except_worsening(tmp_path):
    """По монете — не чаще раза в 6 ч, кроме ухудшения (🟠 → 🔴 сразу; 🔴 → 🔴 — только через 6 ч)."""
    t = [1_794_182_400.0 + 21 * 3600]                           # 08.11 21:00 UTC
    st = _state(tmp_path, ["ADA"])
    dec = Decider(st, now=lambda: t[0])
    assert dec.on_items("CoinDesk", [_item("CoinDesk", "Cardano (ADA) exploit", 0.6)]) == []
    a = dec.on_items("Decrypt", [_item("Decrypt", "Cardano (ADA) exploit confirmed", 0.4)])
    assert [x.kind for x in a] == ["send"] and a[0].text.startswith(ORANGE)
    dec.bound(a[0].event, 1)
    t[0] += 3600                                                # 22:00 — то же событие, официально: ухудшение
    a = dec.on_items("Bybit", [_item("Bybit", "Bybit will suspend ADA deposits and withdrawals", 1.0, "exchange")])
    assert [x.kind for x in a] == ["send"] and a[0].text.startswith(RED)
    t[0] += 3 * 3600                                            # 01:00 следующих суток — новое событие, тот же 🔴
    assert dec.on_items("OKX", [_item("OKX", "OKX will delist ADA", 1.0, "exchange")]) == []
    t[0] += 3 * 3600 + 60                                       # 04:01 — 6 ч после 🔴 прошли
    a = dec.on_items("KuCoin", [_item("KuCoin", "KuCoin will delist ADA", 1.0, "exchange")])
    assert [x.kind for x in a] == ["send"]                     # придержанное событие уходит, с обоими источниками
    assert a[0].text.splitlines()[0] == f"{RED} ADA — OKX: OKX will delist ADA" and a[0].text.endswith("ещё: KuCoin")


def test_big_listing_is_green_small_is_silent(tmp_path):
    st = _state(tmp_path, ["MX", "ADA"])
    dec = Decider(st, now=lambda: NOW)
    a = dec.on_items("Bybit", [_item("Bybit", "New Listing: MX/USDT spot trading", 1.0, kind="exchange")])
    assert a[0].text.startswith(f"{GREEN} MX — Bybit")
    assert dec.on_items("Bitget", [_item("Bitget", "Bitget will list ADA perpetual", 1.0, kind="exchange")]) == []
    assert dec.on_items("CoinDesk", [_item("CoinDesk", "Cardano (ADA) to list on new venue", 0.6)]) == []


def test_short_ticker_in_media_only_by_brackets_or_issuer(tmp_path):
    """«ONE» в СМИ — обычное слово: два источника с ним — молчание. «(ONE)» — монета: два источника — 🟠."""
    st = _state(tmp_path, ["ONE"])
    dec = Decider(st, now=lambda: NOW)
    assert dec.on_items("Decrypt", [_item("Decrypt", "ONE more exchange hacked", 0.4)]) == []
    assert dec.on_items("Bitcoinist", [_item("Bitcoinist", "ONE MORE exploit drains DeFi pool", 0.4)]) == []
    d2 = Decider(_state(tmp_path / "b", ["ONE"]), now=lambda: NOW)
    assert d2.on_items("Decrypt", [_item("Decrypt", "Harmony (ONE) bridge hacked", 0.4)]) == []
    a = d2.on_items("Bitcoinist", [_item("Bitcoinist", "Harmony (ONE) exploit: funds drained", 0.4)])
    assert [x.kind for x in a] == ["send"] and a[0].text.startswith(f"{ORANGE} ONE")


def test_sent_file_forgets_after_seven_days(tmp_path):
    t = [NOW]
    st = State(tmp_path, now=lambda: t[0])
    st.items = {"old": NOW - 8 * 86400, "new": NOW - 86400}
    st.events = {"e": {"ts": NOW - 8 * 86400}}
    st.save_sent()
    again = State(tmp_path, now=lambda: t[0])
    assert again.items == {"new": NOW - 86400} and again.events == {}
    assert (tmp_path / "sent.json").stat().st_size < 4096


def test_quiet_minutes_of_battle_scan():
    assert [m for m in range(60) if C.quiet_minute(m)] == [0, 1, 2, 15, 16, 17, 30, 31, 32, 45, 46, 47]


def test_same_item_next_day_is_not_sent_again(tmp_path):
    """«Уже прислал» — по ключу новости на 7 суток, а не по событию дня: лента держит запись и завтра."""
    t = [NOW]
    st = _state(tmp_path, ["ADA"])
    dec = Decider(st, now=lambda: t[0])
    it = _item("OKX", "OKX will delist ADA", 1.0, kind="exchange")
    assert [a.kind for a in dec.on_items("OKX", [it])] == ["send"]
    t[0] += 25 * 3600 + 6 * 3600
    assert dec.on_items("OKX", [it]) == []


def test_exchange_trouble_red_even_if_token_not_listed_with_token_line(tmp_path):
    """Координатор 2123: 🔴 о самой бирже (FinCEN → MEXC) — сообщение по её токену MX, даже если MX нет в списке;
    строка про токен; событие — в файл проверки вперёд. Объявление биржи о чужой монете — не беда биржи."""
    st = _state(tmp_path, ["ADA"])
    dec = Decider(st, now=lambda: NOW)
    acts = _feed(dec, "FinCEN", "fincen_news.html")
    assert [a.kind for a in acts] == ["send"]
    lines = acts[0].text.splitlines()
    assert lines[0].startswith(f"{RED} MX (эмитент MEXC) — FinCEN") and lines[2].startswith("🟠 токен биржи MX ")
    assert [(e["exchange"], e["token"], e["source"]) for e in dec.exchange_events] == [("MEXC", "MX", "FinCEN")]
    # Слабое (одно СМИ) о бирже — не для токена вне списка.
    assert dec.on_items("Decrypt", [_item("Decrypt", "Bitget faces probe over fees", 0.4)]) == []
    # Своя лента биржи и её объявление о чужой монете — не беда биржи.
    assert dec.on_items("OKX", [_item("OKX", "OKX will delist ADA", 1.0, kind="exchange")])[0].text.startswith(
        f"{RED} ADA — OKX")
    assert dec.on_items("CoinDesk", [_item("CoinDesk", "Binance to delist XYZ token", 0.6)]) == []
    assert len(dec.exchange_events) == 1


def test_forward_log_fills_prices_later_and_keeps_unknown_empty(tmp_path):
    from news_watch.forward import ForwardLog
    t = [NOW]
    log = ForwardLog(tmp_path / "exchange_events.json", now=lambda: t[0])
    log.add({"exchange": "MEXC", "token": "MX", "source": "FinCEN", "title": "x", "url": "u"}, 2.5, None)
    assert [f for _, f in log.due()] == ["0"]                     # BTC в момент не получен — дописать, не ноль
    log.fill(log.rows[0], "0", None, 101_000.0)
    assert log.due() == []
    t[0] += 86400 + 1
    assert [f for _, f in log.due()] == ["1d"]
    log.fill(log.rows[0], "1d", 2.1, 99_000.0)
    t[0] += 6 * 86400
    assert [f for _, f in log.due()] == ["3d", "7d"]
    again = ForwardLog(tmp_path / "exchange_events.json", now=lambda: t[0])
    r = again.rows[0]
    assert (r["token_0"], r["btc_0"], r["token_1d"], r["token_3d"]) == (2.5, 101_000.0, 2.1, None)


def test_exchange_own_feed_and_routine_news_are_not_exchange_trouble(tmp_path):
    st = _state(tmp_path, ["BNB"])
    dec = Decider(st, now=lambda: NOW)
    # Своя лента Binance о чужой монете со словом «exploit» — не беда Binance.
    assert dec.on_items("Binance", [_item("Binance", "Binance will suspend XYZ deposits after exploit", 1.0,
                                          kind="exchange")]) == []
    # Два СМИ про делистинг чужой монеты на Binance — не 🟠 по BNB.
    assert dec.on_items("CoinDesk", [_item("CoinDesk", "Binance to delist XYZ token", 0.6)]) == []
    assert dec.on_items("Decrypt", [_item("Decrypt", "Binance will delist XYZ next week", 0.4)]) == []
    # А беда самой биржи из двух СМИ — 🟠 по BNB.
    dec.on_items("CoinDesk", [_item("CoinDesk", "Binance hit with SEC lawsuit", 0.6)])
    a = dec.on_items("Decrypt", [_item("Decrypt", "Binance faces lawsuit from SEC", 0.4)])
    assert [x.kind for x in a] == ["send"] and a[0].text.startswith(f"{ORANGE} BNB (эмитент Binance)")
