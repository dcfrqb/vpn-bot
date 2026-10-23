"""Telegram transport: retry rejected requests, never replay business handlers."""
from __future__ import annotations

import asyncio
import time

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, AnswerPreCheckoutQuery, GetUpdates

from app.logger import logger


class RetryAfterMiddleware(BaseRequestMiddleware):
    """Only 429 guarantees the request was rejected. Network errors are not retried.

    Bound both retries and waiting. Callback and checkout answers have short
    Telegram deadlines; long polling already has its own retry loop.
    """

    async def __call__(self, make_request, bot, method):
        if isinstance(method, GetUpdates):
            return await make_request(bot, method)
        budget = 3 if isinstance(method, (AnswerCallbackQuery, AnswerPreCheckoutQuery)) else 30
        deadline = time.monotonic() + budget
        for attempt in range(3):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as exc:
                wait = max(0, exc.retry_after)
                if attempt == 2 or time.monotonic() + wait >= deadline:
                    raise
                logger.warning(f"Telegram rate limit: {type(method).__name__}, retry={attempt + 1}, wait={wait}s")
                await asyncio.sleep(wait)


def create_bot(token: str, *, session=None) -> Bot:
    bot = Bot(token=token, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    bot.session.middleware(RetryAfterMiddleware())
    return bot
