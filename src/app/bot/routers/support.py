"""Help, support contact, privacy link.

Owner stream: D (User UI).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.callbacks import Nav
from app.bot.views import render
from app.bot.views import support as support_view
from app.config import settings
from app.domain.texts.common import support_handle

router = Router(name="r3_support")

_HELP_SCREENS = ("help",)


async def show_help_screen(event, *, answer_callback: bool = True, **data) -> None:
    text, markup = support_view.render(
        support_handle=support_handle(settings), privacy_url=settings.PRIVACY_URL,
        unlink_enabled=bool(settings.DEVICES_UNLINK_ENABLED),
    )
    await render(event, text, markup, answer_callback=answer_callback)


@router.callback_query(Nav.filter(F.s.in_(_HELP_SCREENS)))
async def open_help(callback: CallbackQuery, callback_data: Nav, **data) -> None:
    await show_help_screen(callback, **data)
