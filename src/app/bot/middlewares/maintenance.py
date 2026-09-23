"""Maintenance middleware (release 3.0 Foundation).

OUTER middleware on dp.message and dp.callback_query. No-op unless the
MaintenanceGuard says maintenance is active (manual Redis flag in
Foundation; stream C adds the automatic panel probe behind
MAINTENANCE_AUTO_ENABLED). While active, non-admin users get a short
notice and the update stops here; admins pass through.

The guard state is cached in-process for CACHE_SECONDS so a Redis outage
does not add a failing round-trip to every update.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.domain.texts.common import MAINTENANCE
from app.logger import logger

CACHE_SECONDS = 5.0


class MaintenanceMiddleware(BaseMiddleware):
    def __init__(self, guard: Any, admin_ids: Optional[Callable[[], Any]] = None):
        self.guard = guard
        self._admin_ids = admin_ids or self._settings_admins
        self._cached: Optional[bool] = None
        self._cached_at = 0.0

    @staticmethod
    def _settings_admins():
        from app.config import settings

        return settings.ADMINS or []

    async def _active(self) -> bool:
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < CACHE_SECONDS:
            return self._cached
        try:
            active = bool(await self.guard.is_active())
        except Exception as e:  # noqa: BLE001 - fail-open: keep serving
            logger.warning(f"maintenance guard failed ({type(e).__name__}), treating as off")
            active = False
        self._cached, self._cached_at = active, now
        return active

    def reset_cache(self) -> None:
        self._cached = None

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if not await self._active():
            return await handler(event, data)
        user = getattr(event, "from_user", None)
        if user is not None and user.id in set(self._admin_ids()):
            return await handler(event, data)
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(MAINTENANCE, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(MAINTENANCE, parse_mode=None)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"maintenance notice failed ({type(e).__name__})")
        return None
