"""HTTP с предохранителями (план, п. 6, 8).

* не чаще одного запроса к домену за `min_interval` (Bybit — 30 с: адрес общий с боем 2ч);
* 403 / 429 / 418 — домен молчит 30 мин, наружу — «отказ» (одна строка Олегу пишется в main);
* любой другой сбой — None и причина словами, а не пустой ответ (правило 21).
"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

from . import config as C

UA = "news-watch/1.0 (+private alert bot)"
REFUSAL = (403, 418, 429)


class Net:
    def __init__(self, session, now=time.monotonic, sleep=asyncio.sleep):
        self.session, self.now, self.sleep = session, now, sleep
        self.last: dict[str, float] = {}
        self.muted_until: dict[str, float] = {}

    def muted(self, url: str) -> bool:
        return self.now() < self.muted_until.get(urlparse(url).hostname or "", 0)

    async def get(self, url: str, params: dict | None = None) -> tuple[bytes | None, str | None]:
        host = urlparse(url).hostname or ""
        if self.muted(url):
            return None, "молчит после отказа"
        gap = C.DOMAIN_MIN_INTERVAL.get(host, C.DEFAULT_MIN_INTERVAL)
        wait = self.last.get(host, -1e18) + gap - self.now()
        if wait > 0:
            await self.sleep(wait)
        self.last[host] = self.now()
        try:
            async with self.session.get(url, params=params, headers={"User-Agent": UA}) as r:
                body = await r.read()
                if r.status in REFUSAL:
                    self.muted_until[host] = self.now() + C.MUTE_ON_REFUSAL_S
                    return None, f"отказ {r.status}"
                if r.status != 200:
                    return None, f"ответ {r.status}"
                return body, None
        except (asyncio.TimeoutError, OSError) as e:
            return None, f"нет связи ({type(e).__name__})"
        except Exception as e:                      # aiohttp.ClientError и прочее — не роняем цикл
            return None, f"сбой ({type(e).__name__})"
