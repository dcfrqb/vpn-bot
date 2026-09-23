"""Help / support screen view (release 3.0). Owner stream: D (User UI)."""
from __future__ import annotations

from typing import Optional

from aiogram.types import InlineKeyboardMarkup

from app.bot.views import kb, url_btn
from app.bot.views.common import back_to_main_row
from app.domain.texts.common import BTN_OFFER, BTN_PRIVACY, BTN_SUPPORT, HELP_TEXT, OFFER_URL, support_url


def render(*, support_handle: Optional[str] = None, privacy_url: Optional[str] = None) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list] = [[url_btn(BTN_SUPPORT, support_url(support_handle))], [url_btn(BTN_OFFER, OFFER_URL)]]
    if privacy_url:
        rows.append([url_btn(BTN_PRIVACY, privacy_url)])
    rows.append(back_to_main_row())
    return HELP_TEXT, kb(rows)
