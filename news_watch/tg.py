"""Telegram: кнопки «➕ монета», «📋 мои монеты», «➖ убрать» и отправка тревог. Отвечает только Олегу (chat_id из .env)."""
from __future__ import annotations

import json
import re

ADD, LIST, REMOVE = "➕ монета", "📋 мои монеты", "➖ убрать"
KEYBOARD = {"keyboard": [[{"text": ADD}, {"text": LIST}, {"text": REMOVE}]], "resize_keyboard": True}
TICKER_RE = re.compile(r"^[A-Z0-9]{2,15}$")


class Bot:
    def __init__(self, session, token: str, chat_id: str):
        self.session, self.base, self.chat_id = session, f"https://api.telegram.org/bot{token}", str(chat_id)

    async def call(self, method: str, **params):
        async with self.session.post(f"{self.base}/{method}", json=params) as r:
            d = await r.json(content_type=None)
        if not d.get("ok"):
            raise RuntimeError(f"telegram {method}: {d.get('error_code')} {d.get('description')}")
        return d["result"]

    async def send(self, text: str, markup: dict | None = None) -> int:
        res = await self.call("sendMessage", chat_id=self.chat_id, text=text, disable_web_page_preview=True,
                              reply_markup=markup or KEYBOARD)
        return res["message_id"]

    async def edit(self, message_id: int, text: str) -> None:
        await self.call("editMessageText", chat_id=self.chat_id, message_id=message_id, text=text,
                        disable_web_page_preview=True)


class Dialog:
    """Разговор с Олегом. `add_coin(тикер)` → текст ответа; `state` — State (coins.json)."""

    def __init__(self, state, add_coin):
        self.st, self.add_coin, self.waiting = state, add_coin, False

    async def on_update(self, upd: dict, chat_id: str) -> list[tuple[str, dict | None]]:
        """Ответы на одно обновление: [(текст, разметка)]. Чужой чат — пусто."""
        cb = upd.get("callback_query")
        if cb:
            if str(cb.get("message", {}).get("chat", {}).get("id")) != chat_id:
                return []
            data = cb.get("data", "")
            if data.startswith("rm:"):
                t = data[3:]
                if self.st.coins.pop(t, None) is not None:
                    self.st.save_coins()
                    return [(f"✅ {t} убрана", None)]
                return [(f"{t} уже нет в списке", None)]
            return []
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != chat_id:
            return []
        text = (msg.get("text") or "").strip()
        if text in ("/start", LIST):
            self.waiting = False
            return [(self.list_text(), None)]
        if text == ADD:
            self.waiting = True
            return [("Введи тикер монеты (например MX, ADA):", None)]
        if text == REMOVE:
            self.waiting = False
            if not self.st.coins:
                return [("Список пуст.", None)]
            kb = {"inline_keyboard": [[{"text": f"➖ {t}", "callback_data": f"rm:{t}"}] for t in sorted(self.st.coins)]}
            return [("Какую убрать?", kb)]
        if self.waiting:
            t = text.upper().removesuffix("USDT")
            if not TICKER_RE.match(t):
                return [("Не похоже на тикер. Пример: MX", None)]
            self.waiting = False
            if t in self.st.coins:
                return [(f"{t} уже в списке", None)]
            return [(await self.add_coin(t), None)]
        return [("Кнопки внизу: ➕ монета, 📋 мои монеты, ➖ убрать.", None)]

    def list_text(self) -> str:
        if not self.st.coins:
            return "Монет пока нет. Нажми ➕ монета."
        rows = []
        for t, c in sorted(self.st.coins.items()):
            where = f"цена: {c['venue']}" if c.get("venue") else "цена не отслеживается"
            rows.append(f"• {t} — {where}" + (f", эмитент {c['issuer']}" if c.get("issuer") else ""))
        return "Мои монеты:\n" + "\n".join(rows)


def updates_offset(updates: list[dict], offset: int) -> int:
    return max([offset - 1] + [u["update_id"] for u in updates]) + 1


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False)
