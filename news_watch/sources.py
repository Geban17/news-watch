"""Разбор ответов источников в единый вид `Item`. Чистые функции: байты ответа → список записей.

Снимки настоящих ответов (снятых с мака 08.11) — `tests/fixtures/`. Ответ не разобрался — исключение, а не пустой
список: «не смог прочитать» и «новостей нет» — разные ответы (правило 21 боевого проекта).
"""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime


@dataclass(frozen=True)
class Item:
    source: str          # имя источника из config.SOURCES
    kind: str            # exchange | gov | media
    key: str             # id или ссылка — для «уже прислал»
    title: str
    url: str
    ts_ms: int | None
    weight: float


class ParseError(Exception):
    pass


def _ms(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def bybit(raw: bytes) -> list[dict]:
    d = json.loads(raw)
    if d.get("retCode") != 0:
        raise ParseError(f"retCode={d.get('retCode')}")
    return [{"key": r["url"], "title": r["title"], "url": r["url"], "ts_ms": _ms(r.get("publishTime"))}
            for r in d["result"]["list"]]


def okx(raw: bytes) -> list[dict]:
    d = json.loads(raw)
    if str(d.get("code")) != "0":
        raise ParseError(f"code={d.get('code')}")
    return [{"key": r["url"], "title": r["title"], "url": r["url"], "ts_ms": _ms(r.get("pTime"))}
            for page in d["data"] for r in page["details"]]


def bitget(raw: bytes) -> list[dict]:
    d = json.loads(raw)
    if str(d.get("code")) != "00000":
        raise ParseError(f"code={d.get('code')}")
    return [{"key": r["annId"], "title": r["annTitle"], "url": r["annUrl"], "ts_ms": _ms(r.get("cTime"))}
            for r in d["data"]]


def kucoin(raw: bytes) -> list[dict]:
    d = json.loads(raw)
    if str(d.get("code")) != "200000":
        raise ParseError(f"code={d.get('code')}")
    return [{"key": str(r["annId"]), "title": r["annTitle"], "url": r["annUrl"], "ts_ms": _ms(r.get("cTime"))}
            for r in d["data"]["items"]]


def binance_cms(raw: bytes) -> list[dict]:
    d = json.loads(raw)
    if str(d.get("code")) != "000000":
        raise ParseError(f"code={d.get('code')}")
    out = []
    for cat in d["data"]["catalogs"]:
        for a in cat["articles"]:
            out.append({"key": str(a["id"]), "title": a["title"],
                        "url": f"https://www.binance.com/en/support/announcement/detail/{a['code']}",
                        "ts_ms": _ms(a.get("releaseDate"))})
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def rss(raw: bytes) -> list[dict]:
    """RSS 2.0 и Atom: заголовок, ссылка, время."""
    # Чужой XML: объявления сущностей не нужны ни одной ленте — отказ до разбора (XXE / «billion laughs»).
    if b"<!ENTITY" in raw[:4096]:
        raise ParseError("xml: объявление сущностей")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ParseError(f"xml: {e}") from e
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        f = {_local(c.tag): c for c in el}
        title = (f["title"].text or "").strip() if "title" in f else ""
        link = ""
        if "link" in f:
            link = (f["link"].text or "").strip() or f["link"].get("href", "")
        guid = (f["guid"].text or "").strip() if "guid" in f and f["guid"].text else ""
        ts = None
        for k in ("pubDate", "published", "updated"):
            if k in f and f[k].text:
                try:
                    ts = int(parsedate_to_datetime(f[k].text).timestamp() * 1000)
                except (TypeError, ValueError):
                    try:
                        ts = int(datetime.fromisoformat(f[k].text.strip().replace("Z", "+00:00")).timestamp() * 1000)
                    except ValueError:
                        ts = None
                break
        if title:
            out.append({"key": guid or link or title, "title": html.unescape(title), "url": link, "ts_ms": ts})
    return out


def _links(raw: bytes, base: str, pattern: str) -> list[dict]:
    h = raw.decode("utf-8", "replace")
    out, seen = [], set()
    for href, text in re.findall(pattern, h):
        title = html.unescape(re.sub(r"\s+", " ", text)).strip()
        if not title or href in seen:
            continue
        seen.add(href)
        url = base + href if href.startswith("/") else href
        out.append({"key": url, "title": title, "url": url, "ts_ms": None})
    return out


def fincen(raw: bytes) -> list[dict]:
    return _links(raw, "https://www.fincen.gov",
                  r'<a href="(/news/(?:news-releases|press-releases|enforcement-actions)/[^"]+)"[^>]*>([^<]{10,300})</a>')


def ofac(raw: bytes) -> list[dict]:
    rows = _links(raw, "https://ofac.treasury.gov", r'<a href="(/recent-actions/\d{8}[^"]*)"[^>]*>([^<]{5,300})</a>')
    return rows


def cftc(raw: bytes) -> list[dict]:
    return _links(raw, "https://www.cftc.gov", r'href="(/PressRoom/PressReleases/[0-9]+-[0-9]+)"[^>]*>([^<]{10,300})<')


PARSERS = {"bybit": bybit, "okx": okx, "bitget": bitget, "kucoin": kucoin, "binance_cms": binance_cms,
           "rss": rss, "fincen": fincen, "ofac": ofac, "cftc": cftc}


def parse(source: tuple, raw: bytes) -> list[Item]:
    name, kind, parser, _url, _period, weight = source
    rows = PARSERS[parser](raw)
    return [Item(source=name, kind=kind, key=f"{name}|{r['key']}", title=r["title"], url=r["url"],
                 ts_ms=r["ts_ms"], weight=weight) for r in rows]
