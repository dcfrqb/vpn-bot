"""Connect screen: apps, subscription link, article button; trial/buy CTA without a subscription.

Owner stream: D (User UI).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.callbacks import Nav
from app.bot.views import connect as connect_view
from app.bot.views import render
from app.config import settings
from app.domain.models import PromoOutcome
from app.domain.texts import connect as t
from app.logger import logger

router = Router(name="r3_connect")

# ui: alias reuses "connect"; get_subscription_link maps to Nav(s="connect", p="link").
_OPEN_PAYLOADS = ("", "open", "link", "back")


async def show_connect_screen(event, *, answer_callback: bool = True, **data) -> None:
    """Render the connect screen for ``event`` (Message or CallbackQuery)."""
    status_service = data["status_service"]
    promo = data.get("promo")
    user = event.from_user
    state = await status_service.get_state(user.id)

    if not state.active or not state.subscription_url:
        trial_available = False
        try:
            trial_available = bool(promo and await promo.trial_available(user.id))
        except Exception:  # noqa: BLE001 - PromoService may not be shipped yet (owner: E)
            trial_available = False
        text, markup = connect_view.no_subscription(
            trial_available=trial_available, support_handle=settings.SUPPORT_HANDLE,
        )
        await render(event, text, markup, answer_callback=answer_callback)
        return

    text, markup = connect_view.success(state, article_url=settings.CONNECT_ARTICLE_URL)
    await render(event, text, markup, answer_callback=answer_callback)


@router.callback_query(Nav.filter(((F.s == "connect") & F.p.in_(_OPEN_PAYLOADS)) | (F.s == "connect_success")))
async def open_connect(callback: CallbackQuery, callback_data: Nav, **data) -> None:
    await show_connect_screen(callback, **data)


@router.callback_query(Nav.filter((F.s == "connect") & (F.p == "trial")))
async def start_trial(callback: CallbackQuery, callback_data: Nav, **data) -> None:
    promo = data.get("promo")
    status_service = data.get("status_service")
    user = callback.from_user

    if promo is None:
        await callback.answer(t.TRIAL_UNAVAILABLE, show_alert=True)
        return
    try:
        reward = await promo.start_trial(user.id)
    except Exception as e:  # noqa: BLE001 - PromoService may not be shipped yet (owner: E)
        logger.debug(f"r3_connect.start_trial: {type(e).__name__}")
        await callback.answer(t.TRIAL_UNAVAILABLE, show_alert=True)
        return

    if not reward.applied:
        text = {
            PromoOutcome.ALREADY_USED: t.TRIAL_ALREADY_USED,
            PromoOutcome.NOT_ELIGIBLE: t.TRIAL_NOT_ELIGIBLE,
            PromoOutcome.EXHAUSTED: t.TRIAL_NOT_ELIGIBLE,
        }.get(reward.outcome, t.TRIAL_UNAVAILABLE)
        await callback.answer(text, show_alert=True)
        return

    if status_service is not None:
        try:
            await status_service.invalidate(user.id)
        except Exception:  # noqa: BLE001
            pass
    await callback.answer(t.TRIAL_STARTED)
    await show_connect_screen(callback, answer_callback=False, **data)
