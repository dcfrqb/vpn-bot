"""Last new router: any callback no other handler took (retired 2.x screens
such as ui:profile:*, ui:error:*, very old buttons, unknown packed data).

Answers the callback (no endless spinner) and shows the main menu. It is
excluded from the callback matrix "exactly one handler" count.
Owner: D (User UI).
"""
from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery

from app.domain.texts.common import STALE_BUTTON
from app.logger import logger

router = Router(name="r3_fallback")


@router.callback_query()
async def stale_callback(callback: CallbackQuery, **data) -> None:
    from app.bot.routers.menu import show_main_screen

    logger.info(f"fallback: unhandled callback data={str(callback.data)[:64]!r} tg_id={callback.from_user.id}")
    try:
        await callback.answer(STALE_BUTTON)
    except Exception:  # noqa: BLE001 - query too old
        pass
    await show_main_screen(callback, answer_callback=False, **data)
