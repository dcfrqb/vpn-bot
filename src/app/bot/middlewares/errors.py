"""Errors middleware for NEW routers (release 3.0 Foundation).

Attached by app.bot.routers.include_routers() to every 3.0 stream router
(inner middleware on message and callback_query). 2.x routers keep their
own behaviour and the global tg_errors handler.

On an unexpected exception in a 3.0 handler:
  - log it with traceback (logger.exception);
  - tell the user a GENERIC text (domain.texts.common), never the exception;
  - notify admins in the ERRORS topic, deduplicated per handler+type for 10 min;
  - swallow it (the update is considered handled).
Telegram API errors (TelegramAPIError: RetryAfter, Forbidden, BadRequest)
are re-raised so the global tg_errors router handles them as in 2.x.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from app.domain.models import AdminTopic
from app.domain.texts.common import GENERIC_ERROR, GENERIC_ERROR_ALERT
from app.logger import logger

ERROR_DEDUP_TTL = 600


def _handler_name(data: dict[str, Any]) -> str:
    h = data.get("handler")
    cb = getattr(h, "callback", None)
    return getattr(cb, "__qualname__", None) or getattr(cb, "__name__", None) or "unknown"


class ErrorsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramAPIError:
            raise
        except Exception as exc:  # noqa: BLE001
            name = _handler_name(data)
            user = getattr(event, "from_user", None)
            uid = getattr(user, "id", None)
            logger.exception(f"r3 handler {name} failed for user={uid}: {type(exc).__name__}")
            await self._tell_user(event)
            notifier = data.get("notifier")
            if notifier is not None:
                try:
                    await notifier.notify_admins(
                        AdminTopic.ERRORS,
                        f"Ошибка в {name}: {type(exc).__name__}: {str(exc)[:300]}\nuser={uid}",
                        dedup_key=f"err:{name}:{type(exc).__name__}",
                        dedup_ttl=ERROR_DEDUP_TTL,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"errors middleware: admin notify failed ({type(e).__name__})")
            return None

    @staticmethod
    async def _tell_user(event: Any) -> None:
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(GENERIC_ERROR_ALERT, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(GENERIC_ERROR, parse_mode=None)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"errors middleware: cannot answer user ({type(e).__name__})")
