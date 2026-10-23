"""Global fallback: acknowledge errors without replaying business operations.

Rate-limit retries happen at the outgoing session, not at the update handler.
Only unchanged-message errors are silently ignored.
"""
from __future__ import annotations

from typing import Any

from aiogram import Router
from aiogram.exceptions import (
    TelegramForbiddenError,
)
from aiogram.types import ErrorEvent

from app.logger import logger


router = Router(name="tg_errors_global")


@router.errors()
async def global_errors_handler(event: ErrorEvent, **data: Any) -> bool:
    from app.bot.middlewares.errors import ErrorsMiddleware, is_unchanged_message

    exc = event.exception
    update = event.update
    if is_unchanged_message(exc):
        return True
    if isinstance(exc, TelegramForbiddenError):
        user_id = _extract_user_id(update)
        logger.warning(f"TelegramForbiddenError: tg_id={user_id}, update_id={update.update_id}")
        # Missing permissions in a group/admin chat do not mean the user blocked us.
        if user_id and any(reason in exc.message.lower() for reason in (
            "bot was blocked by the user", "user is deactivated",
        )):
            await _mark_inactive(user_id)
        return True

    target = update.callback_query or update.pre_checkout_query or update.message
    await ErrorsMiddleware.report(target, exc, data)
    return True


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
