"""Проверка вперёд «токен биржи после её беды» (координатор 2123): одна строка на событие — десятки строк в год.

`state/exchange_events.json` — список строк: дата, биржа, токен, источник, цена токена и BTC в момент, и через 1 / 3 / 7
дней (дописывает сам бот). Цена не получена — поле остаётся пустым и пробуется на следующем обороте, а не
записывается нулём. Отчёт по файлу — раз в месяц координатору (делает Боевая с копии файла, не бот).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import config as C


class ForwardLog:
    def __init__(self, path: Path, now=time.time):
        self.path, self.now = path, now
        try:
            self.rows: list[dict] = json.loads(path.read_text())
        except FileNotFoundError:
            self.rows = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.rows, ensure_ascii=False, indent=1))
        os.replace(tmp, self.path)

    def add(self, ev: dict, token_px: float | None, btc_px: float | None) -> None:
        now = self.now()
        self.rows.append({"ts": int(now), "date": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now)), **ev,
                          "token_0": token_px, "btc_0": btc_px,
                          **{f"{k}_{d}d": None for d in C.FORWARD_DAYS for k in ("token", "btc")}})
        self.save()

    def due(self) -> list[tuple[dict, str]]:
        """(строка, поле) — пора дописать цену: день наступил, поле пустое. Цена в момент — тоже, если не получена."""
        out = []
        for r in self.rows:
            if r.get("token_0") is None or r.get("btc_0") is None:
                out.append((r, "0"))
            for d in C.FORWARD_DAYS:
                if self.now() >= r["ts"] + d * 86400 and (r.get(f"token_{d}d") is None or r.get(f"btc_{d}d") is None):
                    out.append((r, f"{d}d"))
        return out

    def fill(self, row: dict, field: str, token_px: float | None, btc_px: float | None) -> None:
        if token_px is not None:
            row[f"token_{field}"] = token_px
        if btc_px is not None:
            row[f"btc_{field}"] = btc_px
        self.save()
