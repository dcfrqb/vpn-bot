"""Shared error feedback for handlers and the global dispatcher fallback.

Only unchanged-message errors are harmless. Forbidden errors reach the global
handler, which records blocked users without attempting another message.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

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
        except TelegramForbiddenError:
            raise
        except Exception as exc:  # noqa: BLE001
            if is_unchanged_message(exc):
                return None
            await self.report(event, exc, data)
            return None

    @staticmethod
    async def report(event: Any, exc: Exception, data: dict[str, Any]) -> None:
        name = _handler_name(data)
        user = getattr(event, "from_user", None)
        uid = getattr(user, "id", None)
        logger.opt(exception=exc).error(f"handler {name} failed for user={uid}: {type(exc).__name__}")
        await ErrorsMiddleware._tell_user(event)
        notifier = data.get("notifier")
        if notifier is not None:
            try:
                from app.domain.texts import ui

                await notifier.notify_admins(
                    AdminTopic.ERRORS,
                    ui.admin_alert(f"Ошибка в {ui.h(name)}", emoji="❌", lines=[
                        ui.field("Тип", type(exc).__name__), ui.field("Текст", str(exc)[:300]),
                        ui.field("user", uid)]).html(),
                    html=True, dedup_key=f"err:{name}:{type(exc).__name__}",
                    dedup_ttl=ERROR_DEDUP_TTL,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"errors middleware: admin notify failed ({type(e).__name__})")
        return None

    @staticmethod
    async def _tell_user(event: Any) -> None:
        if isinstance(event, CallbackQuery):
            try:
                await event.answer(GENERIC_ERROR_ALERT, show_alert=True)
                return
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"errors middleware: alert unavailable ({type(exc).__name__})")
            # A stale/answered callback can no longer display an alert. Send a
            # fresh message in the original chat, including inaccessible messages.
            chat = getattr(event.message, "chat", None)
            chat_id = chat.id if chat is not None else event.from_user.id
            try:
                await event.bot.send_message(chat_id, GENERIC_ERROR, parse_mode="HTML")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"errors middleware: fallback unavailable ({type(exc).__name__})")
            return
        try:
            if isinstance(event, PreCheckoutQuery):
                await event.answer(ok=False, error_message=GENERIC_ERROR_ALERT)
            elif isinstance(event, Message):
                await event.answer(GENERIC_ERROR, parse_mode="HTML")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"errors middleware: cannot answer user ({type(exc).__name__})")


def is_unchanged_message(exc: Exception) -> bool:
    return isinstance(exc, TelegramBadRequest) and "message is not modified" in exc.message.lower()
