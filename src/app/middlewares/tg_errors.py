"""
Global error handler для aiogram Dispatcher.

Покрывает три класса Telegram ошибок:
- `TelegramRetryAfter` — flood-control, ждём `retry_after` и отпускаем update
  (без re-dispatch, т.к. Telegram сам повторит при polling/webhook).
- `TelegramForbiddenError` — юзер заблокировал бота → помечаем
  `telegram_users.is_active=False`, чтобы broadcast/уведомления его пропускали.
- `TelegramBadRequest` — логируем и подавляем (чаще всего "message is not modified",
  "message to edit not found" — безвредные гонки UI).

Прочие ошибки прокидываются дальше, чтобы aiogram сам их залогировал с трассой.
"""
from __future__ import annotations

import asyncio
from typing import Any

from aiogram import Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import ErrorEvent

from app.logger import logger


router = Router(name="tg_errors_global")


@router.errors()
async def global_errors_handler(event: ErrorEvent) -> bool:
    exc = event.exception
    update = event.update

    if isinstance(exc, TelegramRetryAfter):
        logger.warning(
            f"TelegramRetryAfter: ждём {exc.retry_after}s перед следующим апдейтом "
            f"(update_id={getattr(update, 'update_id', None)})"
        )
        try:
            await asyncio.sleep(exc.retry_after + 1)
        except asyncio.CancelledError:
            raise
        return True

    if isinstance(exc, TelegramForbiddenError):
        user_id = _extract_user_id(update)
        logger.info(
            f"TelegramForbiddenError: юзер заблокировал бота, tg_id={user_id}"
        )
        if user_id:
            await _mark_inactive(user_id)
        return True

    if isinstance(exc, TelegramBadRequest):
        logger.warning(
            f"TelegramBadRequest (подавлено): {exc} "
            f"update_id={getattr(update, 'update_id', None)}"
        )
        return True

    # Остальные исключения — дать aiogram их отлогировать как unhandled.
    return False


def _extract_user_id(update: Any) -> int | None:
    for path in ("message.from_user.id", "callback_query.from_user.id"):
        obj: Any = update
        try:
            for attr in path.split("."):
                obj = getattr(obj, attr, None)
                if obj is None:
                    break
            if isinstance(obj, int):
                return obj
        except Exception:
            continue
    return None


async def _mark_inactive(user_id: int) -> None:
    """Проставляет is_active=False для юзера. Ошибки БД не поднимаем — это best-effort."""
    try:
        from sqlalchemy import update as sa_update

        from app.db.models import TelegramUser
        from app.db.session import SessionLocal

        if not SessionLocal:
            return
        async with SessionLocal() as session:
            await session.execute(
                sa_update(TelegramUser)
                .where(TelegramUser.telegram_id == user_id)
                .values(is_active=False)
            )
            await session.commit()
    except Exception as e:
        logger.warning(f"Не удалось пометить is_active=False для tg_id={user_id}: {e}")
