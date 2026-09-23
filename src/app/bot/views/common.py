"""Shared keyboard rows for D screens (release 3.0).

Owner stream: D (User UI).
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton

from app.bot.callbacks import Nav
from app.bot.views import btn, url_btn
from app.domain.texts.common import BTN_BACK_MAIN


def back_to_main_row() -> list[InlineKeyboardButton]:
    return [btn(BTN_BACK_MAIN, Nav(s="main"))]


def support_row(handle: "str | None") -> list[InlineKeyboardButton]:
    from app.domain.texts.common import BTN_SUPPORT, support_url

    return [url_btn(BTN_SUPPORT, support_url(handle))]
