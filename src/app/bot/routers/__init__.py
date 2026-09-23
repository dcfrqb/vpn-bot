"""Router order of release 3.0. FROZEN seam.

Order matters: aiogram gives an update to the first matching handler.

  1. site_login (2.x module, unchanged) - /start login_* and sitelogin: must
     never reach any other router;
  2. NEW 3.0 routers in NEW_ROUTER_MODULES order (promo_deeplink BEFORE
     start so /start <code> is seen first);
  3. 2.x routers in their 2.1.1 order (LEGACY_ROUTER_MODULES), deleted by
     the cutover agent;
  4. the global Telegram errors router.

New routers get ErrorsMiddleware (generic text to the user, never the
exception). Legacy callback strings are rewritten to packed callbacks by
app.bot.legacy_aliases before routing, only when a NEW router accepts them.

To add a router module: orchestrator commit adding it to NEW_ROUTER_MODULES.
"""
from __future__ import annotations

import importlib
from typing import Iterable

from aiogram import Dispatcher, Router

SITE_LOGIN_MODULE = "app.routers.site_login"

NEW_ROUTER_MODULES: tuple[str, ...] = (
    "app.bot.routers.promo_deeplink",
    "app.bot.routers.start",
    "app.bot.routers.menu",
    "app.bot.routers.checkout",
    "app.bot.routers.connect",
    "app.bot.routers.devices",
    "app.bot.routers.support",
    "app.bot.routers.refund",
    "app.bot.routers.admin.payments",
    "app.bot.routers.admin.promo",
    "app.bot.routers.admin.broadcast",
    "app.bot.routers.admin.obhod",
    "app.bot.routers.admin.panel",
)

# 2.x routers in the exact 2.1.1 order of app.main.setup_dispatcher.
# app.routers.payments is intentionally NOT registered (duplicate pay_yookassa_).
LEGACY_ROUTER_MODULES: tuple[str, ...] = (
    "app.routers.ui",
    "app.routers.start",
    "app.legacy.routers.payments",
    "app.routers.admin_broadcast",
    "app.routers.admin",
    "app.routers.legacy_callbacks",
)

ERRORS_ROUTER_MODULE = "app.middlewares.tg_errors"

ROUTERS: tuple[str, ...] = (
    SITE_LOGIN_MODULE,
    *NEW_ROUTER_MODULES,
    *LEGACY_ROUTER_MODULES,
    ERRORS_ROUTER_MODULE,
)


def _router(module: str) -> Router:
    return importlib.import_module(module).router


def new_routers() -> list[Router]:
    return [_router(m) for m in NEW_ROUTER_MODULES]


def legacy_routers() -> list[Router]:
    return [_router(m) for m in LEGACY_ROUTER_MODULES]


def _attach_errors_middleware(routers: Iterable[Router]) -> None:
    from app.bot.middlewares.errors import ErrorsMiddleware

    for r in routers:
        if getattr(r, "_r3_errors_mw", False):
            continue
        mw = ErrorsMiddleware()
        r.message.middleware(mw)
        r.callback_query.middleware(mw)
        r.pre_checkout_query.middleware(mw)
        r._r3_errors_mw = True


def include_routers(dp: Dispatcher) -> None:
    """Include every router in ROUTERS order into ``dp``."""
    dp.include_router(_router(SITE_LOGIN_MODULE))
    fresh = new_routers()
    _attach_errors_middleware(fresh)
    for r in fresh:
        dp.include_router(r)
    for r in legacy_routers():
        dp.include_router(r)
    dp.include_router(_router(ERRORS_ROUTER_MODULE))
