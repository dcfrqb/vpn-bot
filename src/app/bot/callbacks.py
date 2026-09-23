"""All callback_data classes of release 3.0. FROZEN seam.

Every inline button of the new UI packs one of these classes. Telegram limits
callback_data to 64 bytes; aiogram's pack() raises if exceeded and
tests/test_r3_callbacks.py checks worst-case values of every class.

Conventions:
- Short prefixes (1-2 chars) and short field names keep room for payloads.
- Field values must not contain ":" (the separator). Use "." inside ``arg``.
- Money never travels in callback_data: prices come from domain.plans.
- Legacy 2.x strings are rewritten to these classes by bot/legacy_aliases.py.
- ``Bc(prefix="bc", a=...)`` is byte-identical to the 2.x broadcast buttons
  ``bc:unsub`` / ``bc:close``: a new handler on Bc.filter() takes them over.

Adding a class or field = orchestrator commit (see docs/ARCHITECTURE_3.0.md).
Owner stream of each class is noted in its docstring.
"""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData

# Prefixes of 2.x callbacks that are NOT CallbackData. A new prefix must not
# start any of these strings (test_r3_callbacks checks it).
LEGACY_STRING_PREFIXES = (
    "ui:", "check_payment", "rv_ok:", "rv_no:", "sitelogin:", "pay_yookassa_",
    "plan_", "admin_", "friend_", "back_to_main", "buy_subscription",
    "connect_vpn", "get_subscription_link", "help", "refresh_info", "my_plan",
)


class Nav(CallbackData, prefix="n"):
    """Stateless navigation. s = screen id, p = optional payload. Owner: D.

    Screens used by aliases: main, plans, connect, help, plan, friend_req,
    and every 2.x ui ScreenID value (ui:<screen>:<action>:<payload> ->
    Nav(s=<screen>, p="<action>" or "<action>.<payload>")).
    """

    s: str
    p: str = ""


class Plan(CallbackData, prefix="pl"):
    """Plan chosen on the plans screen. c = plan code. Owner: A."""

    c: str


class Period(CallbackData, prefix="pe"):
    """Period chosen -> checkout. c = plan code, m = months. Owner: A."""

    c: str
    m: int


class PayCheck(CallbackData, prefix="pc"):
    """«Проверить оплату». pid = payments.id; ext = provider id when pid is
    unknown (legacy check_payment:<external_id>). Owner: A."""

    pid: int = 0
    ext: str = ""


class PayStars(CallbackData, prefix="ps"):
    """Pay with Telegram Stars. Owner: A."""

    c: str
    m: int


class AutoPay(CallbackData, prefix="ap"):
    """Autorenew toggle: a in {on, off, info}. Owner: A."""

    a: str


class Dev(CallbackData, prefix="dv"):
    """Devices: a in {list, ask, unlink, back}; id = DeviceInfo.short_id. Owner: B/D."""

    a: str
    id: str = ""


class RefundReq(CallbackData, prefix="rr"):
    """User asks for a 24h refund of payments.id = pid. Owner: A."""

    pid: int


class AdmRefund(CallbackData, prefix="ar"):
    """Admin decision on refund_requests.id = rid: a in {ok, no}. Owner: A."""

    a: str
    rid: int


class AdmReview(CallbackData, prefix="av"):
    """Admin review of a held payment (2.x rv_ok:/rv_no:): a in {ok, no}. Owner: A."""

    a: str
    pid: int


class PromoAct(CallbackData, prefix="pr"):
    """User-side promo actions: a in {enter, trial, apply}; arg = code. Owner: E."""

    a: str
    arg: str = ""


class PromoAdm(CallbackData, prefix="pa"):
    """Admin promo-code management: a = action, id = promo_codes.id. Owner: E."""

    a: str
    id: int = 0
    arg: str = ""


class Adm(CallbackData, prefix="ad"):
    """Admin panel: s = section, a = action, arg = payload ("." separated).

    Sections used by aliases: panel, stats, users, payments, access, friend,
    promo_req. Owner: E (sections of other streams live under their own s).
    """

    s: str
    a: str
    arg: str = ""


class Bc(CallbackData, prefix="bc"):
    """Buttons under a broadcast message: a in {unsub, close}. Same bytes as 2.x. Owner: E."""

    a: str


class BcAdm(CallbackData, prefix="ba"):
    """Admin broadcast wizard: a = step/action, id = broadcasts.id. Owner: E."""

    a: str
    id: int = 0
    arg: str = ""


class Gift(CallbackData, prefix="gf"):
    """Gifts: a in {buy, plan, period, claim}; id = code or payload. Owner: A/E."""

    a: str
    id: str = ""


ALL_CALLBACKS: tuple[type[CallbackData], ...] = (
    Nav, Plan, Period, PayCheck, PayStars, AutoPay, Dev, RefundReq, AdmRefund,
    AdmReview, PromoAct, PromoAdm, Adm, Bc, BcAdm, Gift,
)
