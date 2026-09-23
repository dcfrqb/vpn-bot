"""Admin guard for 3.0 admin routers (stream E).

An INNER middleware on the admin router's message and callback_query
observers: filters still match for everybody (so a callback resolves to
exactly one handler, see tests/flows/test_callback_matrix.py), but only ids
from settings.ADMINS reach the handler. Others get «Недостаточно прав»
(callbacks, as an alert) or nothing (commands), and the attempt is logged.

    router = Router(name="r3_admin_x")
    guard_router(router)
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Router
from aiogram.types import CallbackQuery, Message

from app.logger import logger

NO_RIGHTS = "Недостаточно прав"


def is_admin_id(user_id: Any) -> bool:
    from app.config import settings

    try:
        return int(user_id) in (settings.ADMINS or [])
    except (TypeError, ValueError):
        return False


class AdminGuard(BaseMiddleware):
    async def __call__(self, handler: Callable[[Any, dict[str, Any]], Awaitable[Any]], event: Any,
                       data: dict[str, Any]) -> Any:
        user = getattr(event, "from_user", None)
        if user is not None and is_admin_id(user.id):
            return await handler(event, data)
        uid = getattr(user, "id", None)
        if isinstance(event, CallbackQuery):
            logger.warning(f"admin guard: callback by non-admin user_id={uid} data={event.data!r}")
            try:
                await event.answer(NO_RIGHTS, show_alert=True)
            except Exception:  # noqa: BLE001
                pass
        elif isinstance(event, Message):
            logger.info(f"admin guard: command by non-admin user_id={uid}")
        return None


def guard_router(router: Router) -> Router:
    guard = AdminGuard()
    router.message.middleware(guard)
    router.callback_query.middleware(guard)
    return router


__all__ = ["AdminGuard", "guard_router", "is_admin_id", "NO_RIGHTS"]
