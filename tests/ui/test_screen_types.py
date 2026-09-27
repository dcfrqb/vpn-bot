"""Every user screen is built by the kit (docs/SCREENS.md): a known type, closed tags,
no letter yo, no em dash, toasts within 200 characters, the offer line under a bill."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import pytest

from app.bot.views import connect as connect_view
from app.bot.views import devices as devices_view
from app.bot.views import kit
from app.bot.views import menu as menu_view
from app.bot.views import money as money_view
from app.bot.views import support as support_view
from app.domain.models import DeviceInfo, PromoOutcome, PromoReward, SubscriptionState
from app.domain.texts import checkout, common, connect, devices, menu, notify, promo, ui

NOW = datetime.now(timezone.utc)
TEXT_MODULES = (checkout, common, connect, devices, menu, notify, promo)
ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "blockquote", "a"}


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.bad: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.bad.append(tag)
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.bad.append("/" + tag)


def assert_well_formed(html: str) -> None:
    p = _Tags()
    p.feed(html)
    assert not p.bad and not p.stack, (p.bad, p.stack, html)
    assert "ё" not in html and "Ё" not in html
    assert "—" not in html, html  # no em dash in user copy
    assert "\n\n\n" not in html and html == html.strip()


def _module_screens():
    for mod in TEXT_MODULES:
        for name, value in vars(mod).items():
            if isinstance(value, ui.Screen):
                yield f"{mod.__name__}.{name}", value


@pytest.mark.parametrize("name,screen", list(_module_screens()), ids=lambda x: x if isinstance(x, str) else "")
def test_module_screens_follow_the_rules(name, screen):
    assert screen.type in ui.TYPES
    assert_well_formed(screen.html())


def test_toasts_are_short_plain_text():
    toasts = [common.GENERIC_ERROR_ALERT, common.REFRESHED, common.STALE_BUTTON, connect.TRIAL_STARTED,
              connect.TRIAL_ALREADY_USED, connect.TRIAL_NOT_ELIGIBLE, connect.TRIAL_UNAVAILABLE, connect.TRIAL_BUSY,
              devices.UNLINKED, devices.UNLINK_LIMIT, checkout.PAYMENT_BUSY, checkout.STARS_UNAVAILABLE,
              checkout.GIFTS_UNAVAILABLE, checkout.AUTOPAY_UNAVAILABLE, checkout.STARS_INVOICE_SENT,
              checkout.PRECHECK_STALE, checkout.PRECHECK_PRICE_CHANGED, checkout.check_rate_limited(30),
              promo.CODES_DISABLED, promo.FRIEND_USE_COMMAND, notify.MAINTENANCE_SCREEN,
              checkout.PLAN_UNAVAILABLE, checkout.PAYMENT_BLOCKED, checkout.PAYMENT_CREATE_FAILED,
              checkout.OBHOD_NEEDS_PRO]
    for t in toasts:
        assert len(t) <= ui.TOAST_MAX and "<" not in t and "ё" not in t, t


def _states():
    return [
        SubscriptionState(telegram_id=1),
        SubscriptionState(telegram_id=1, active=True, plan_code="pro", expires_at=NOW + timedelta(days=5),
                          device_limit=10, devices_used=2, obhod_active=True, obhod_subscription_url="https://o/x",
                          subscription_url="https://s/x", obhod_limit_bytes=10 ** 11),
        SubscriptionState(telegram_id=1, active=True, plan_code="pro", expires_at=NOW + timedelta(days=5),
                          subscription_url="https://s/x"),
        SubscriptionState(telegram_id=1, active=False, plan_code="lite", expires_at=NOW - timedelta(days=1),
                          grace_until=NOW + timedelta(days=1), subscription_url="https://s/x"),
        SubscriptionState(telegram_id=1, active=False, plan_code="lite", expires_at=NOW - timedelta(days=3)),
    ]


def _user_views():
    dev = DeviceInfo(hwid="a" * 24, platform="iOS", device_model="iPhone <15>", updated_at=NOW)
    for st in _states():
        yield menu_view.render(1, "<Имя>", st, is_admin=True, trial_available=not st.active)
        if st.subscription_url:
            yield connect_view.success(st, article_url="https://t/a")
    yield connect_view.no_subscription(trial_available=True)
    yield connect_view.error("dcfrq")
    yield devices_view.list_screen([dev], device_limit=3, unlink_enabled=True)
    yield devices_view.list_screen([], device_limit=None, unlink_enabled=False, active=False)
    yield devices_view.ask_unlink(dev)
    yield devices_view.not_found()
    yield support_view.render(unlink_enabled=True)
    plans = [money_view.PlanOption("pro", "Pro", ("Все серверы",), 449)]
    yield money_view.plans_view(plans, gifts=True)
    yield money_view.plans_view(plans, gifts=True, gift=True)
    yield money_view.periods_view("pro", "Pro", ["Все серверы"], [money_view.PeriodOption(3, 1199, 11)])
    yield money_view.obhod_packages_view(100, [("obhod_250", "Обход 250 ГБ / мес", 599)])
    yield money_view.obhod_packages_view(100, [])
    yield money_view.checkout_view(plan_code="pro", name="Pro", months=1, amount_rub=449, payment_id=7,
                                   url="https://pay/x", autorenew=True, stars=250)
    yield money_view.message_view(checkout.CHECK_PENDING_SCREEN, pay_url="https://pay/x", amount_rub=449,
                                  check_pid=7, support="https://t.me/x")


@pytest.mark.parametrize("v", list(_user_views()), ids=lambda v: v.type)
def test_user_views_are_kit_views(v):
    assert isinstance(v, kit.View) and v.type in ui.TYPES
    assert_well_formed(v.text)
    rows = v.markup.inline_keyboard
    labels = [b.text for row in rows for b in row]
    assert "⬅️ В главное меню" not in labels  # one name: «🏠 В меню»
    if v.type not in ("status", "confirm", "push"):
        assert rows and any(b.text == ui.B.MENU for b in rows[-1]), labels  # the footer is the last row


def test_checkout_has_the_offer_line_with_links_from_settings():
    text, _ = money_view.checkout_view(plan_code="lite", name="Lite", months=1, amount_rub=129, payment_id=1,
                                       url="https://pay/x", autorenew=None, stars=None,
                                       offer_url="https://example.com/offer", privacy_url=None)
    assert "Нажимая «Оплатить», ты принимаешь условия" in text
    assert '<a href="https://example.com/offer">оферты</a>' in text
    assert re.search(r'<a href="https://telegra.ph/Politika-konfidencialnosti[^"]*">политики', text)


def test_promo_results_are_result_screens():
    ok = PromoReward(code="trial", outcome=PromoOutcome.APPLIED, plan_code="standard", days=5, expires_at=NOW)
    assert promo.applied_screen("trial", ok, plan_title="Standard").type == "result"
    for o in PromoOutcome:
        if o is PromoOutcome.APPLIED:
            continue
        s = promo.outcome_screen("<x>", PromoReward(code="<x>", outcome=o))
        assert s.type == "result"
        assert_well_formed(s.html())


def test_pushes_are_html_screens():
    for s in (checkout.paid_user_screen("Pro", 1, NOW), checkout.autopay_notice_screen("Pro", 1, 449, NOW),
              checkout.gift_paid_buyer_screen("Pro", 1, "https://t.me/b?start=g_1"),
              checkout.refund_done_screen(NOW, expired=False), notify.reminder_screen("3d", NOW),
              notify.grace_started_screen(3, NOW, 5), notify.device_added_screen("<Mac>", 1, 2, "@dcfrq"),
              notify.obhod_limited_screen(10 ** 11, True), promo.referral_bonus_screen(1, 2, 1),
              promo.referral_new_payment_screen(3, 10, 0.4, 1), promo.referral_payout_screen(1, "<n>", 0)):
        assert s.type == "push"
        assert_well_formed(s.html())
