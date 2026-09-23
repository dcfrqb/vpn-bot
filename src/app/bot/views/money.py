"""Money screens and keyboards (release 3.0, stream A). Pure functions of DTOs.

Also ``TelegramMoneyUi``: the keyboards money services attach to messages
they send themselves (app.services.payments.ui.MoneyUi).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import AdmRefund, AdmReview, AutoPay, Gift, Nav, PayCheck, PayStars, Period, Plan, RefundReq
from app.bot.views import kb, url_btn
from app.domain.texts import checkout as T


@dataclass(frozen=True)
class PeriodOption:
    months: int
    amount_rub: int
    saving_percent: int = 0


@dataclass(frozen=True)
class PlanOption:
    code: str
    name: str
    features: tuple[str, ...]
    from_rub: Optional[int]


def support_url(settings: Any) -> Optional[str]:
    from app.domain.texts.common import support_handle

    handle = support_handle(settings)
    if not handle:
        return None
    return f"https://t.me/{str(handle).strip().lstrip('@')}"


# --- screens -------------------------------------------------------------------------------


def plans_view(plans: Sequence[PlanOption], *, gifts: bool, gift: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    text = T.plans_screen([(p.name, p.features) for p in plans], gift=gift)
    rows: list[list[Any]] = []
    for p in plans:
        cb = Gift(a="plan", id=p.code) if gift else Plan(c=p.code)
        rows.append([(T.btn_plan(p.name, p.from_rub), cb)])
    if gifts and not gift:
        rows.append([(T.BTN_GIFT, Gift(a="buy"))])
    rows.append([(T.BTN_BACK, Nav(s="plans")) if gift else (T.BTN_BACK_MAIN, Nav(s="main"))])
    return text, kb(rows)


def periods_view(plan_code: str, name: str, features: Sequence[str], options: Sequence[PeriodOption], *,
                 gift: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list[Any]] = []
    for o in options:
        cb = Gift(a="period", id=f"{plan_code}.{o.months}") if gift else Period(c=plan_code, m=o.months)
        rows.append([(T.btn_period(o.months, o.amount_rub, o.saving_percent), cb)])
    rows.append([(T.BTN_BACK, Gift(a="buy") if gift else Nav(s="plans"))])
    return T.periods_screen(name, features, gift=gift), kb(rows)


def obhod_packages_view(base_gb: int, packages: Sequence[tuple[str, str, int]]) -> tuple[str, InlineKeyboardMarkup]:
    """packages: [(code, display, price)]; a package is bought as Period(c=<code>, m=1)."""
    rows: list[list[Any]] = [[(T.btn_obhod_package(name, price), Period(c=code, m=1))]
                             for code, name, price in packages]
    rows.append([(T.BTN_BACK, Nav(s="plans"))])
    return T.obhod_packages_screen(base_gb, [(name, price) for _c, name, price in packages]), kb(rows)


def checkout_view(*, plan_code: str, name: str, months: int, amount_rub: int, payment_id: int, url: str,
                  autorenew: Optional[bool], stars: Optional[int], gift: bool = False,
                  back: Any = None) -> tuple[str, InlineKeyboardMarkup]:
    """autorenew None = no autorenew line and no toggle (AUTOPAY_ENABLED off / gift)."""
    rows: list[list[Any]] = [[url_btn(T.btn_pay(amount_rub), url)]]
    if stars and not gift:
        rows.append([(T.btn_pay_stars(stars), PayStars(c=plan_code, m=months))])
    if autorenew is not None:
        rows.append([(T.BTN_AUTOPAY_OFF if autorenew else T.BTN_AUTOPAY_ON, AutoPay(a="off" if autorenew else "on"))])
    rows.append([(T.BTN_CHECK, PayCheck(pid=payment_id))])
    rows.append([(T.BTN_BACK, back or (Gift(a="plan", id=plan_code) if gift else Plan(c=plan_code)))])
    text = T.checkout_screen(name, months, amount_rub, autorenew=autorenew, gift=gift,
                             stars=stars if not gift else None)
    return text, kb(rows)


def message_view(text: str, *, back_to_plans: bool = True, support: Optional[str] = None,
                 connect: bool = False, pay_url: Optional[str] = None, amount_rub: Optional[int] = None,
                 check_pid: Optional[int] = None) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list[Any]] = []
    if pay_url and amount_rub:
        rows.append([url_btn(T.btn_pay(amount_rub), pay_url)])
    if check_pid:
        rows.append([(T.BTN_CHECK, PayCheck(pid=check_pid))])
    if connect:
        rows.append([(T.BTN_CONNECT, Nav(s="connect"))])
    if support:
        rows.append([url_btn(T.BTN_SUPPORT, support)])
    if back_to_plans:
        rows.append([(T.BTN_PLANS, Nav(s="plans"))])
    rows.append([(T.BTN_BACK_MAIN, Nav(s="main"))])
    return text, kb(rows)


def paid_kb(payment_id: int, *, refund_button: bool) -> InlineKeyboardMarkup:
    rows: list[list[Any]] = [[(T.BTN_CONNECT, Nav(s="connect"))]]
    if refund_button:
        rows.append([(T.BTN_REFUND, RefundReq(pid=payment_id))])
    return kb(rows)


# --- MoneyUi for services ------------------------------------------------------------------


class TelegramMoneyUi:
    """app.services.payments.ui.MoneyUi over aiogram keyboards."""

    def __init__(self, settings: Any = None):
        self._settings = settings

    @property
    def settings(self):
        if self._settings is not None:
            return self._settings
        from app.config import settings

        return settings

    def paid(self, payment_id: int, *, refund_button: bool) -> Any:
        return paid_kb(payment_id, refund_button=refund_button)

    def gift_paid(self) -> Any:
        return kb([[(T.BTN_BACK_MAIN, Nav(s="main"))]])

    def review_admin(self, payment_id: int) -> Any:
        return kb([[(T.BTN_REVIEW_OK, AdmReview(a="ok", pid=payment_id)),
                    (T.BTN_REVIEW_NO, AdmReview(a="no", pid=payment_id))]])

    def refund_admin(self, request_id: int) -> Any:
        return kb([[(T.BTN_REFUND_OK, AdmRefund(a="ok", rid=request_id)),
                    (T.BTN_REFUND_NO, AdmRefund(a="no", rid=request_id))]])

    def autopay_notice(self) -> Any:
        return kb([[(T.BTN_AUTOPAY_STOP, AutoPay(a="stop"))]])

    def renew(self) -> Any:
        return kb([[(T.BTN_RENEW, Nav(s="plans"))]])

    def support(self) -> Any:
        url = support_url(self.settings)
        return kb([[url_btn(T.BTN_SUPPORT, url)]]) if url else None


__all__ = [
    "PeriodOption", "PlanOption", "TelegramMoneyUi", "checkout_view", "message_view", "paid_kb",
    "periods_view", "plans_view", "support_url",
]
