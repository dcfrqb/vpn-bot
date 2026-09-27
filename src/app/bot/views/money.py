"""Money screens and keyboards (release 3.0, stream A). Pure functions of DTOs.

Also ``TelegramMoneyUi``: the keyboards money services attach to messages
they send themselves (app.services.payments.ui.MoneyUi).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import AdmRefund, AdmReview, AutoPay, Gift, Nav, PayCheck, PayStars, Period, Plan, RefundReq
from app.bot.views import kit
from app.domain.texts import checkout as T
from app.domain.texts.common import OFFER_URL, PRIVACY_URL
from app.domain.texts.ui import B, Screen


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


# --- screens (docs/SCREENS.md: choice, checkout, result) ----------------------------------


def plans_view(plans: Sequence[PlanOption], *, gifts: bool, gift: bool = False) -> kit.View:
    screen = T.plans_screen_of([(p.name, p.features, p.code) for p in plans], gift=gift)
    return kit.view(
        screen,
        options=[kit.action(T.btn_plan(p.name, p.from_rub), Gift(a="plan", id=p.code) if gift else Plan(c=p.code))
                 for p in plans],
        secondary=[kit.action(T.BTN_GIFT, Gift(a="buy")) if gifts and not gift else None],
        footer=kit.Footer.back_menu(Nav(s="plans")) if gift else kit.Footer.to_menu(),
    )


def periods_view(plan_code: str, name: str, features: Sequence[str], options: Sequence[PeriodOption], *,
                 gift: bool = False) -> kit.View:
    return kit.view(
        T.periods_screen_of(name, features, gift=gift, code=plan_code),
        options=[kit.action(T.btn_period(o.months, o.amount_rub, o.saving_percent),
                            Gift(a="period", id=f"{plan_code}.{o.months}") if gift else Period(c=plan_code, m=o.months))
                 for o in options],
        footer=kit.Footer.back_menu(Gift(a="buy") if gift else Nav(s="plans")),
    )


def obhod_packages_view(base_gb: int, packages: Sequence[tuple[str, str, int]]) -> kit.View:
    """packages: [(code, display, price)]; a package is bought as Period(c=<code>, m=1)."""
    return kit.view(
        T.obhod_packages_screen_of(base_gb, [(name, price) for _c, name, price in packages]),
        options=[kit.action(T.btn_obhod_package(name, price), Period(c=code, m=1)) for code, name, price in packages],
        footer=kit.Footer.back_menu(Nav(s="plans")),
    )


def checkout_view(*, plan_code: str, name: str, months: int, amount_rub: int, payment_id: int, url: str,
                  autorenew: Optional[bool], stars: Optional[int], gift: bool = False,
                  back: Any = None, offer_url: Optional[str] = None,
                  privacy_url: Optional[str] = None) -> kit.View:
    """autorenew None = no autorenew line and no toggle (AUTOPAY_ENABLED off / gift)."""
    screen = T.checkout_screen_of(name, months, amount_rub, autorenew=autorenew, gift=gift,
                                  stars=stars if not gift else None,
                                  offer_url=offer_url or OFFER_URL, privacy_url=privacy_url or PRIVACY_URL)
    return kit.view(
        screen,
        primary=[kit.link(T.btn_pay(amount_rub), url)],
        secondary=[
            kit.action(T.btn_pay_stars(stars), PayStars(c=plan_code, m=months)) if stars and not gift else None,
            kit.action(T.BTN_AUTOPAY_OFF if autorenew else T.BTN_AUTOPAY_ON, AutoPay(a="off" if autorenew else "on"))
            if autorenew is not None else None,
            kit.action(T.BTN_CHECK, PayCheck(pid=payment_id)),
        ],
        footer=kit.Footer.back_menu(back or (Gift(a="plan", id=plan_code) if gift else Plan(c=plan_code))),
    )


def message_view(screen: Screen, *, back_to_plans: bool = True, support: Optional[str] = None,
                 connect: bool = False, pay_url: Optional[str] = None, amount_rub: Optional[int] = None,
                 check_pid: Optional[int] = None) -> kit.View:
    """A ``result`` screen on the way to or after a payment."""
    return kit.view(
        screen,
        primary=[
            kit.link(T.btn_pay(amount_rub), pay_url) if pay_url and amount_rub else None,
            kit.action(T.BTN_CHECK, PayCheck(pid=check_pid)) if check_pid else None,
            kit.action(T.BTN_CONNECT, Nav(s="connect")) if connect else None,
        ],
        secondary=[kit.action(T.BTN_PLANS, Nav(s="plans")) if back_to_plans else None],
        links=[kit.link(T.BTN_SUPPORT, support) if support else None],
        footer=kit.Footer.to_menu(),
    )


def paid_kb(payment_id: int, *, refund_button: bool) -> InlineKeyboardMarkup:
    return kit.keyboard(primary=[kit.action(T.BTN_CONNECT, Nav(s="connect"))],
                        secondary=[kit.action(T.BTN_REFUND, RefundReq(pid=payment_id)) if refund_button else None])


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
        return kit.keyboard(footer=kit.Footer.to_menu())

    def review_admin(self, payment_id: int) -> Any:
        """Decision row of the «held» payment alert (screen kit ``kit.pair``)."""
        return kit.markup_only(primary=[kit.pair(
            kit.action(B.APPROVE.format(label="Одобрить и выдать"), AdmReview(a="ok", pid=payment_id)),
            kit.action(B.REJECT.format(label="Отклонить"), AdmReview(a="no", pid=payment_id)))])

    def refund_admin(self, request_id: int) -> Any:
        """Decision row of the 24h refund request alert (screen kit ``kit.pair``)."""
        return kit.markup_only(primary=[kit.pair(
            kit.action(B.APPROVE.format(label="Вернуть"), AdmRefund(a="ok", rid=request_id)),
            kit.action(B.REJECT.format(label="Отклонить"), AdmRefund(a="no", rid=request_id)))])

    def autopay_notice(self) -> Any:
        return kit.markup_only(primary=[kit.action(T.BTN_AUTOPAY_STOP, AutoPay(a="stop"))])

    def renew(self) -> Any:
        return kit.markup_only(primary=[kit.action(T.BTN_RENEW, Nav(s="plans"))])

    def support(self) -> Any:
        url = support_url(self.settings)
        return kit.markup_only(links=[kit.link(T.BTN_SUPPORT, url)]) if url else None


__all__ = [
    "PeriodOption", "PlanOption", "TelegramMoneyUi", "checkout_view", "message_view", "paid_kb",
    "periods_view", "plans_view", "support_url",
]
