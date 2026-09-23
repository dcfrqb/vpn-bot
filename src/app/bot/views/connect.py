"""Connect screen view (release 3.0). Owner stream: D (User UI)."""
from __future__ import annotations

from typing import Optional

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import Nav
from app.bot.views import btn, kb, url_btn
from app.bot.views.common import back_to_main_row, support_row
from app.domain.models import SubscriptionState
from app.domain.plans import OBHOD_PACKAGE_CODES, is_obhod_package_purchasable
from app.domain.texts import connect as t
from app.domain.texts.common import BTN_ARTICLE, BTN_REFRESH, BTN_SUBSCRIPTION, BTN_TRIAL

REFRESH = Nav(s="connect", p="refresh")


def loading() -> tuple[str, InlineKeyboardMarkup]:
    return t.LOADING, kb([])


def error(support_handle: Optional[str] = None) -> tuple[str, InlineKeyboardMarkup]:
    """Panel unreachable or no link for a live subscription (review UX M1):
    never the «not active, buy» screen for a paying user."""
    rows = [[btn(BTN_REFRESH, REFRESH)], support_row(support_handle), back_to_main_row()]
    return t.ERROR, kb(rows)


def no_subscription(*, trial_available: bool, support_handle: Optional[str] = None) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list] = []
    if trial_available:
        rows.append([btn(BTN_TRIAL, Nav(s="connect", p="trial"))])
    rows.append([btn(BTN_SUBSCRIPTION, Nav(s="plans"))])
    rows.append(support_row(support_handle))
    rows.append(back_to_main_row())
    text = t.NO_SUBSCRIPTION if trial_available else t.NO_SUBSCRIPTION_NO_TRIAL
    return text, kb(rows)


def success(
    state: SubscriptionState,
    *,
    article_url: Optional[str] = None,
) -> tuple[str, InlineKeyboardMarkup]:
    url = state.subscription_url or ""
    text = t.success(url)

    is_pro = state.plan_code == "pro"
    if not is_pro:
        text += t.OBHOD_PRO_ONLY
    elif not state.obhod_active or not state.obhod_subscription_url:
        text += t.OBHOD_PREPARING
    if state.grace_until is not None and not state.active:
        text += t.grace_note(state.grace_until)
    else:
        text += t.obhod_ready(state.obhod_subscription_url, state.obhod_used_bytes, state.obhod_limit_bytes)

    rows: list[list] = [[url_btn("🔗 Открыть основную ссылку", url)]] if url else []
    if is_pro and state.obhod_active and state.obhod_subscription_url:
        rows.append([url_btn("🛡 Открыть ссылку обхода", state.obhod_subscription_url)])
    if is_pro and any(is_obhod_package_purchasable(c) for c in OBHOD_PACKAGE_CODES):
        rows.append([btn("➕ Нужно больше обхода", Nav(s="plans", p="obhod"))])
    if is_pro and not (state.obhod_active and state.obhod_subscription_url):
        rows.append([btn(BTN_REFRESH, REFRESH)])  # «нажми «Обновить»» in OBHOD_PREPARING
    if article_url:
        rows.append([url_btn(BTN_ARTICLE, article_url)])
    rows.append(back_to_main_row())
    return text, kb(rows)
