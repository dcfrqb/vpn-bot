"""Last new router: any callback no other handler took (retired 2.x screens
such as ui:profile:*, ui:error:*, very old buttons, unknown packed data).

Answers the callback (no endless spinner) and shows the main menu. It is
excluded from the callback matrix "exactly one handler" count.
Owner: D (User UI).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import Nav
from app.bot.views import kit, render
from app.domain.texts.common import STALE_BUTTON, UNKNOWN_MESSAGE_SCREEN
from app.domain.texts.ui import B
from app.logger import logger

router = Router(name="r3_fallback")


@router.message(F.chat.type == "private", StateFilter(None))
async def unknown_message(message: Message) -> None:
    text, markup = kit.view(
        UNKNOWN_MESSAGE_SCREEN,
        primary=[kit.action(B.HELP, Nav(s="help"))],
        footer=kit.Footer.to_menu(),
    )
    await render(message, text, markup)


@router.callback_query()
async def stale_callback(callback: CallbackQuery, **data) -> None:
    from app.bot.routers.menu import show_main_screen

    logger.info(f"fallback: unhandled callback data={str(callback.data)[:64]!r} tg_id={callback.from_user.id}")
    try:
        await callback.answer(STALE_BUTTON)
    except Exception:  # noqa: BLE001 - query too old
        pass
    await show_main_screen(callback, answer_callback=False, **data)
