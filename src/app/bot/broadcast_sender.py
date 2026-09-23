"""Telegram side of broadcasts (stream E): aiogram BroadcastSender + keyboard.

services.broadcast owns recipients, counters and credits and imports no
aiogram; this module sends one message and classifies the outcome:
  - TelegramRetryAfter -> sleep retry_after+1 and retry (3 attempts);
  - TelegramForbiddenError, "chat not found", "user is deactivated",
    "bot was blocked" -> blocked (the user is marked inactive, no retry);
  - network/timeout -> one more attempt after 10 s, then failed;
  - anything else -> failed.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import Bc
from app.logger import logger
from app.services import broadcast as svc

_BLOCKED_MARKERS = ("chat not found", "user is deactivated", "bot was blocked")


def build_markup(buttons_json: Optional[list[dict]]) -> InlineKeyboardMarkup:
    """Admin buttons (url or callback_data; invalid ones dropped) plus the
    system row «Отписаться | Закрыть» (bc:unsub / bc:close, same bytes as 2.x)."""
    rows: list[list[InlineKeyboardButton]] = []
    for b in buttons_json or []:
        text = (b or {}).get("text") or "→"
        if b.get("url"):
            rows.append([InlineKeyboardButton(text=text, url=b["url"])])
        elif b.get("callback_data"):
            rows.append([InlineKeyboardButton(text=text, callback_data=b["callback_data"])])
    rows.append([
        InlineKeyboardButton(text=svc.UNSUB_BUTTON_TEXT, callback_data=Bc(a="unsub").pack()),
        InlineKeyboardButton(text=svc.CLOSE_BUTTON_TEXT, callback_data=Bc(a="close").pack()),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class AiogramBroadcastSender:
    def __init__(self, bot: Any, *, retry_delay: float = svc.GENERIC_RETRY_DELAY):
        self.bot = bot
        self.retry_delay = retry_delay

    async def _send_raw(self, chat_id: int, text_html: str, photo: Optional[str], markup: Any, silent: bool) -> None:
        for attempt in range(svc.RETRY_AFTER_MAX_ATTEMPTS):
            try:
                if photo:
                    await self.bot.send_photo(chat_id=chat_id, photo=photo, caption=text_html, parse_mode="HTML",
                                              reply_markup=markup, disable_notification=silent)
                else:
                    await self.bot.send_message(chat_id=chat_id, text=text_html, parse_mode="HTML",
                                                reply_markup=markup, disable_notification=silent)
                return
            except TelegramRetryAfter as e:
                logger.warning(f"broadcast RetryAfter user={chat_id} wait={e.retry_after + 1}s attempt={attempt + 1}")
                await asyncio.sleep(e.retry_after + 1)
        raise TimeoutError("RetryAfter exhausted")

    async def send(self, user_id: int, *, text_html: str, photo_file_id: Optional[str],
                   buttons: Optional[list[dict]], disable_notification: bool) -> svc.SendResult:
        markup = build_markup(buttons)
        try:
            await self._send_raw(user_id, text_html, photo_file_id, markup, disable_notification)
            return svc.SendResult("sent")
        except TelegramForbiddenError as e:
            return svc.SendResult("blocked", f"forbidden: {e.message}")
        except TelegramBadRequest as e:
            msg = (e.message or "").lower()
            status = "blocked" if any(m in msg for m in _BLOCKED_MARKERS) else "failed"
            return svc.SendResult(status, e.message)
        except (TelegramNetworkError, TimeoutError, asyncio.TimeoutError):
            await asyncio.sleep(self.retry_delay)
            try:
                await self._send_raw(user_id, text_html, photo_file_id, markup, disable_notification)
                return svc.SendResult("sent")
            except Exception as e2:  # noqa: BLE001
                return svc.SendResult("failed", f"retry_failed: {type(e2).__name__}")
        except Exception as e:  # noqa: BLE001
            return svc.SendResult("failed", f"unexpected: {type(e).__name__}")

    async def preview(self, chat_id: int, info: "svc.BroadcastInfo") -> svc.SendResult:
        return await self.send(chat_id, text_html=info.text_html, photo_file_id=info.photo_file_id,
                               buttons=info.buttons, disable_notification=info.disable_notification)


__all__ = ["build_markup", "AiogramBroadcastSender"]
