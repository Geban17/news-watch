"""Два крошечных файла вместо базы (план, п. 2): `coins.json` — монеты Олега, `sent.json` — «уже прислал».

`sent.json`: {"items": {ключ: ts}, "events": {событие: {...}}, "coins": {тикер: {...}}, "primed": {источник: ts}}.
Записи старше 7 суток вычищаются при каждой записи. Запись атомарная: tmp + rename.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import config as C


def _load(path: Path, empty):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return empty
    except (OSError, ValueError):
        # Испорченный файл не превращается молча в «пусто»: падаем, systemd поднимет, ошибка — в журнал.
        raise


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True))
    os.replace(tmp, path)


class State:
    def __init__(self, folder: Path = C.STATE_DIR, now=time.time):
        self.folder, self.now = folder, now
        self.coins: dict[str, dict] = _load(folder / "coins.json", {})
        s = _load(folder / "sent.json", {})
        self.items: dict[str, float] = s.get("items", {})
        self.events: dict[str, dict] = s.get("events", {})
        self.coin_last: dict[str, dict] = s.get("coins", {})
        self.primed: dict[str, float] = s.get("primed", {})
        self.broken: set[str] = set(s.get("broken", []))                 # «перестал читаться» уже сказано

    def save_coins(self) -> None:
        _save(self.folder / "coins.json", self.coins)

    def save_sent(self) -> None:
        cut = self.now() - C.KEEP_DAYS * 86400
        self.items = {k: t for k, t in self.items.items() if t >= cut}
        self.events = {k: e for k, e in self.events.items() if e["ts"] >= cut}
        self.coin_last = {k: e for k, e in self.coin_last.items() if e["ts"] >= cut}
        _save(self.folder / "sent.json", {"items": self.items, "events": self.events, "coins": self.coin_last,
                                          "primed": self.primed, "broken": sorted(self.broken)})
