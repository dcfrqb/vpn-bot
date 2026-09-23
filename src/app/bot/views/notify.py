"""Keyboards under push notifications (reminders, grace, panel events). Owner: C.

Pure functions; buttons carry packed callbacks from app.bot.callbacks only.
"""
from __future__ import annotations

from typing import Optional

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import Adm, Dev, Nav, Period
from app.bot.views import kb, url_btn
from app.domain.texts import notify as T

# Obhod traffic packages live only on the 2.x plans screen so far. The 2.x
# string is used on purpose: bot.legacy_aliases rewrites it to
# Nav(s="subscription_plans", p=...) once a 3.0 handler accepts that, and
# until then the 2.x ui router opens the packages screen. A packed Nav with
# no 3.0 handler would land in the 2.x catch-all instead.
OBHOD_PACKAGES_CB = "ui:subscription_plans:obhod:-"


def renew_kb(plan_code: Optional[str], months: Optional[int]) -> InlineKeyboardMarkup:
    """«Продлить подписку»: straight to checkout of the last plan and period
    (the price is quoted again by the checkout handler), else to plan choice."""
    if plan_code and months:
        return kb([[(T.BTN_RENEW, Period(c=plan_code, m=int(months)))]])
    return kb([[(T.BTN_RENEW, Nav(s="plans"))]])


def devices_kb() -> InlineKeyboardMarkup:
    return kb([[(T.BTN_DEVICES, Dev(a="list"))]])


def connect_kb(article_url: Optional[str] = None) -> InlineKeyboardMarkup:
    rows = [[(T.BTN_CONNECT, Nav(s="connect"))]]
    if article_url:
        rows.append([url_btn(T.BTN_ARTICLE, article_url)])
    return kb(rows)


def obhod_packages_kb() -> InlineKeyboardMarkup:
    return kb([[(T.BTN_OBHOD_PACKAGES, OBHOD_PACKAGES_CB)]])


def maintenance_admin_kb(active: bool) -> InlineKeyboardMarkup:
    if active:
        return kb([[(T.BTN_MAINT_OFF, Adm(s="maint", a="off"))]])
    return kb([[(T.BTN_MAINT_ON, Adm(s="maint", a="on"))]])
