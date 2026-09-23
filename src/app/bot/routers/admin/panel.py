"""Admin: maintenance toggle (command /maintenance, callbacks Adm s=maint).

Owner stream: C (Panel events). Registered in the order of
app.bot.routers.NEW_ROUTER_MODULES, before every 2.x router.
Thin handlers: ports from DI (``maintenance``), texts from
app.domain.texts.notify, keyboards from app.bot.views.notify.
Admin-only: a non-admin never matches these handlers (the update falls
through to the other routers as before).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, Filter
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import Adm
from app.bot.views import render
from app.bot.views.notify import maintenance_admin_kb
from app.domain.texts import notify as T
from app.logger import logger

router = Router(name="r3_admin_panel")

SECTION = "maint"
MANUAL_REASON = "вручную"


class IsAdmin(Filter):
    async def __call__(self, event) -> bool:
        from app.config import is_admin

        user = getattr(event, "from_user", None)
        return bool(user) and is_admin(user.id)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _screen(maintenance) -> tuple[str, object]:
    from app.config import settings

    active = bool(await maintenance.is_active())
    reason = await maintenance.reason() if active else None
    text = T.admin_maintenance_state(active, reason, bool(settings.MAINTENANCE_AUTO_ENABLED))
    return text, maintenance_admin_kb(active)


@router.message(Command("maintenance"))
async def maintenance_cmd(message: Message, maintenance) -> None:
    text, markup = await _screen(maintenance)
    await render(message, text, markup, parse_mode=None)


@router.callback_query(Adm.filter(F.s == SECTION))
async def maintenance_cb(callback: CallbackQuery, callback_data: Adm, maintenance) -> None:
    from app.services.maintenance import is_auto_reason, suppress_auto

    if callback_data.a == "on":
        await maintenance.set_active(True, reason=MANUAL_REASON, by=callback.from_user.id)
    elif callback_data.a == "off":
        if await maintenance.is_active() and is_auto_reason(await maintenance.reason()):
            await suppress_auto()  # the probe would switch it back on while the panel is down
        await maintenance.set_active(False, by=callback.from_user.id)
    logger.info(f"admin {callback.from_user.id}: maintenance {callback_data.a}")
    text, markup = await _screen(maintenance)
    await render(callback, text, markup, parse_mode=None)

