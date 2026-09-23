"""Connect screen: apps, subscription link, article button; trial/buy CTA without a subscription.

Owner stream: D (User UI).
"""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.bot.callbacks import Nav
from app.bot.views import connect as connect_view
from app.bot.views import render
from app.config import settings
from app.domain.models import PromoOutcome, ensure_utc
from app.domain.texts import connect as t
from app.domain.texts.common import support_handle
from app.logger import logger

router = Router(name="r3_connect")

# ui: alias reuses "connect"; get_subscription_link maps to Nav(s="connect", p="link").
_OPEN_PAYLOADS = ("", "open", "link", "back")


def _in_grace(state) -> bool:
    return state.grace_until is not None and ensure_utc(state.grace_until) > datetime.now(timezone.utc)


async def show_connect_screen(event, *, answer_callback: bool = True, force: bool = False, **data) -> None:
    """Render the connect screen for ``event`` (Message or CallbackQuery)."""
    status_service = data["status_service"]
    promo = data.get("promo")
    user = event.from_user
    state = await (status_service.get_state(user.id, force=True) if force else status_service.get_state(user.id))
    live = state.active or _in_grace(state)  # grace: the link still works (review UX M5)

    if live and state.subscription_url:
        text, markup = connect_view.success(state, article_url=settings.CONNECT_ARTICLE_URL)
        await render(event, text, markup, answer_callback=answer_callback)
        return
    if live or state.stale:
        # Review UX M1: a paying user whose link we cannot show right now (panel
        # down, no URL) gets «try again», never «not active, buy».
        text, markup = connect_view.error(support_handle(settings))
        await render(event, text, markup, answer_callback=answer_callback)
        return

    trial_available = False
    try:
        trial_available = bool(promo and await promo.trial_available(user.id))
    except Exception:  # noqa: BLE001 - PromoService may not be shipped yet (owner: E)
        trial_available = False
    text, markup = connect_view.no_subscription(
        trial_available=trial_available, support_handle=support_handle(settings),
    )
    await render(event, text, markup, answer_callback=answer_callback)


@router.callback_query(Nav.filter(((F.s == "connect") & F.p.in_(_OPEN_PAYLOADS)) | (F.s == "connect_success")))
async def open_connect(callback: CallbackQuery, callback_data: Nav, **data) -> None:
    await show_connect_screen(callback, **data)


@router.callback_query(Nav.filter((F.s == "connect") & (F.p == "refresh")))
async def refresh_connect(callback: CallbackQuery, callback_data: Nav, **data) -> None:
    """«🔄 Обновить» on the connect screen: re-read the panel (review UX M2)."""
    await show_connect_screen(callback, force=True, **data)


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
            PromoOutcome.BUSY: t.TRIAL_BUSY,
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
