"""Shared keyboard rows (release 3.0). New code uses app.bot.views.kit (docs/SCREENS.md).

Owner stream: D (User UI).
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton

from app.bot.views import kit


def back_to_main_row() -> list[InlineKeyboardButton]:
    return kit.Footer.to_menu().row()


def support_row(handle: "str | None") -> list[InlineKeyboardButton]:
    from app.domain.texts.common import BTN_SUPPORT, support_url

    return [kit.link(BTN_SUPPORT, support_url(handle))]
