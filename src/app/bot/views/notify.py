"""Keyboards under push notifications (reminders, grace, panel events). Owner: C.

Type ``push`` (docs/SCREENS.md): 1-2 action buttons, no footer.
Pure functions; buttons carry packed callbacks from app.bot.callbacks only.
"""
from __future__ import annotations

from typing import Optional

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import Adm, Dev, Nav, Period
from app.bot.views import kb, kit
from app.domain.texts import notify as T

# The 3.0 packages screen (review UX m24: no legacy hit per press).
OBHOD_PACKAGES_CB = Nav(s="plans", p="obhod")


def renew_kb(plan_code: Optional[str], months: Optional[int]) -> InlineKeyboardMarkup:
    """«Продлить подписку»: straight to checkout of the last plan and period
    (the price is quoted again by the checkout handler), else to plan choice."""
    cb = Period(c=plan_code, m=int(months)) if plan_code and months else Nav(s="plans")
    return kit.keyboard(primary=[kit.action(T.BTN_RENEW, cb)])


def devices_kb() -> InlineKeyboardMarkup:
    return kit.keyboard(primary=[kit.action(T.BTN_DEVICES, Dev(a="list"))])


def connect_kb(article_url: Optional[str] = None) -> InlineKeyboardMarkup:
    return kit.keyboard(primary=[kit.action(T.BTN_CONNECT, Nav(s="connect"))],
                        links=[kit.link(T.BTN_ARTICLE, article_url) if article_url else None])


def obhod_packages_kb() -> InlineKeyboardMarkup:
    return kit.keyboard(primary=[kit.action(T.BTN_OBHOD_PACKAGES, OBHOD_PACKAGES_CB)])


def maintenance_admin_kb(active: bool) -> InlineKeyboardMarkup:
    if active:
        return kb([[(T.BTN_MAINT_OFF, Adm(s="maint", a="off"))]])
    return kb([[(T.BTN_MAINT_ON, Adm(s="maint", a="on"))]])
