"""Maintenance middleware (release 3.0; Foundation seam, logic by stream C).

OUTER middleware on dp.message and dp.callback_query, before the legacy
alias rewrite, so it sees both packed 3.0 callbacks and raw 2.x strings.
No-op unless the MaintenanceGuard says maintenance is active (manual admin
flag, or the automatic panel probe of app.services.maintenance).

While active, for non-admin users:
  - panel-dependent actions are stopped with the maintenance notice:
    trial, connect / subscription link, devices;
  - checkout stays open: the update goes on to its handler and the user
    gets one short notice that payment works and access comes after the
    works (once per user per NOTICE_EVERY_S, in-process);
  - everything else (menu, help, support, /start) passes untouched.
Admins always pass. The guard state is cached in-process for CACHE_SECONDS
so a Redis outage does not add a failing round-trip to every update; a
failing guard means "not active" (fail-open).
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.domain.texts.notify import MAINTENANCE_CHECKOUT_NOTICE, MAINTENANCE_SCREEN
from app.logger import logger

CACHE_SECONDS = 5.0
NOTICE_EVERY_S = 600.0

BLOCKED = "blocked"
CHECKOUT = "checkout"
PASS = "pass"

# Packed 3.0 callbacks (prefixes from app.bot.callbacks) and 2.x strings.
_BLOCKED_CALLBACKS = (
    "dv:",                    # Dev: devices list / unlink
    "pr:trial",               # PromoAct(a="trial")
    "n:connect",              # Nav(s="connect"...)
    "connect_vpn",            # 2.x
    "get_subscription_link",  # 2.x
    "ui:connect:",            # 2.x connect screen
    "ui:connect_success:",    # 2.x (deprecated screen)
)
_CHECKOUT_CALLBACKS = (
    "pl:", "pe:", "pc:", "ps:", "ap:",                  # Plan, Period, PayCheck, PayStars, AutoPay
    "n:plans",                                          # plan list
    "plan_", "pay_yookassa_", "check_payment", "buy_subscription",  # 2.x
    "ui:subscription_plans:", "ui:subscription_plan_detail:", "ui:subscription_payment:",
)
_BLOCKED_COMMANDS = ("/trial", "/connect", "/devices")


def classify_callback(data: Optional[str]) -> str:
    data = data or ""
    if data.startswith(_BLOCKED_CALLBACKS):
        return BLOCKED
    if data.startswith(_CHECKOUT_CALLBACKS):
        return CHECKOUT
    return PASS


def classify_message(text: Optional[str]) -> str:
    cmd = (text or "").strip().split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
    return BLOCKED if cmd in _BLOCKED_COMMANDS else PASS


class MaintenanceMiddleware(BaseMiddleware):
    def __init__(self, guard: Any, admin_ids: Optional[Callable[[], Any]] = None):
        self.guard = guard
        self._admin_ids = admin_ids or self._settings_admins
        self._cached: Optional[bool] = None
        self._cached_at = 0.0
        self._noticed: dict[int, float] = {}

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
        self._noticed.clear()

    def _should_notice(self, user_id: int) -> bool:
        now = time.monotonic()
        last = self._noticed.get(user_id)
        if last is not None and now - last < NOTICE_EVERY_S:
            return False
        if len(self._noticed) > 10000:
            self._noticed.clear()
        self._noticed[user_id] = now
        return True

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

        if isinstance(event, CallbackQuery):
            kind = classify_callback(event.data)
        elif isinstance(event, Message):
            kind = classify_message(event.text)
        else:
            kind = PASS

        if kind == PASS:
            return await handler(event, data)
        if kind == CHECKOUT:
            if user is not None and self._should_notice(user.id):
                try:
                    await event.bot.send_message(user.id, MAINTENANCE_CHECKOUT_NOTICE, parse_mode=None)
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"maintenance checkout notice failed ({type(e).__name__})")
            return await handler(event, data)

        try:
            if isinstance(event, CallbackQuery):
                await event.answer(MAINTENANCE_SCREEN, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(MAINTENANCE_SCREEN, parse_mode=None)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"maintenance notice failed ({type(e).__name__})")
        return None
