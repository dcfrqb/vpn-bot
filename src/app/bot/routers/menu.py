"""Main menu / status card, Nav(s=main|main_menu|plan), stateless Back and Refresh.

Owner stream: D (User UI).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.callbacks import Nav
from app.bot.views import menu as menu_view
from app.bot.views import render
from app.config import is_admin
from app.domain.texts.common import REFRESHED
from app.utils.html import safe_format_user_name

router = Router(name="r3_menu")

# ui: alias reuses the 2.x screen ids; back_to_main/refresh_info map to "main".
_MAIN_SCREENS = ("main", "main_menu", "plan")


async def show_main_screen(event, *, force: bool = False, answer_callback: bool = True, notice: "str | None" = None, **data) -> None:
    """Render the main menu on ``event`` (Message or CallbackQuery). Reused by
    r3_start (/start) and by other D routers that return to the main menu."""
    status_service = data["status_service"]
    promo = data.get("promo")
    user = event.from_user
    state = await status_service.get_state(user.id, force=force)

    trial_available = False
    if not state.active:
        try:
            trial_available = bool(promo and await promo.trial_available(user.id))
        except Exception:  # noqa: BLE001 - PromoService may not be shipped yet (owner: E)
            trial_available = False

    name = safe_format_user_name(user.first_name, user.last_name, user.username, user.id)
    text, markup = menu_view.render(
        user.id, name, state, is_admin=is_admin(user.id), trial_available=trial_available,
    )
    await render(event, text, markup, answer_callback=answer_callback)
    if notice and isinstance(event, CallbackQuery):
        try:
            await event.answer(notice)
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(Nav.filter(F.s.in_(_MAIN_SCREENS) & (F.p != "refresh")))
async def show_main(callback: CallbackQuery, callback_data: Nav, **data) -> None:
    await show_main_screen(callback, force=False, **data)


@router.callback_query(Nav.filter(F.s.in_(_MAIN_SCREENS) & (F.p == "refresh")))
async def refresh_main(callback: CallbackQuery, callback_data: Nav, **data) -> None:
    await show_main_screen(callback, force=True, answer_callback=False, notice=REFRESHED, **data)
