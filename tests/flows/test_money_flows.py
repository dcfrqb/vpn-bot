"""Stream A money flows through the real Dispatcher: plans, checkout, check, Stars, refunds, review."""
import itertools
import time

import pytest
from aiogram.types import Chat, Message, PreCheckoutQuery, SuccessfulPayment, Update

from app.bot.callbacks import AdmRefund, AdmReview, AutoPay, Nav, PayCheck, PayStars, Period, Plan, RefundReq
from app.domain.plans import get_plan_price
from app.domain.texts import fmt_rub
from tests.fakes.bot import user
from tests.money.fakes import FakeHooks, FakeProvisioning, InMemoryPaymentStore

ADMIN = 424242
_ids = itertools.count(int(time.time()) % 100000 * 1000 + 500)


@pytest.fixture
def money_flow(flow, monkeypatch):
    from app.bot.views.money import TelegramMoneyUi
    from app.services.money import money

    for name, value in {"AUTOPAY_ENABLED": False, "STARS_ENABLED": False, "STARS_RATE": 0.0,
                        "GIFTS_ENABLED": False, "REFUND_24H_ENABLED": False, "ADMINS": [ADMIN],
                        "ADMIN_CHAT_ID": None, "SUPPORT_HANDLE": "crs_support"}.items():
        monkeypatch.setattr(f"app.config.settings.{name}", value)
    store = InMemoryPaymentStore()
    flow.container.provisioning = FakeProvisioning(store)
    m = money(flow.container, store=store, ui=TelegramMoneyUi(), hooks=FakeHooks(), clock=store.clock)
    flow.money = m
    flow.store = store
    return flow


def _last_screen(flow):
    edits = flow.session.calls_of("EditMessageText") or flow.session.calls_of("SendMessage")
    return edits[-1]


def _buttons(call):
    return [b for row in (call.keyboard or []) for b in row]


async def test_buy_subscription_alias_opens_plans_and_periods(money_flow):
    f = money_flow
    await f.press("buy_subscription")
    screen = _last_screen(f)
    datas = [b["data"] for b in _buttons(screen)]
    assert [d for d in datas if d and d.startswith("pl:")] == ["pl:lite", "pl:standard", "pl:pro"]
    assert "Выбери тариф" in screen.text
    await f.press(Plan(c="pro").pack())
    periods = [b for b in _buttons(_last_screen(f)) if (b["data"] or "").startswith("pe:")]
    assert [b["data"] for b in periods] == ["pe:pro:1", "pe:pro:3", "pe:pro:6", "pe:pro:12"]
    assert fmt_rub(get_plan_price("pro", 12)) in periods[-1]["text"]


async def test_period_shows_one_url_button_and_no_autorenew_when_flag_off(money_flow):
    f = money_flow
    await f.press(Period(c="standard", m=3).pack())
    screen = _last_screen(f)
    urls = [b for b in _buttons(screen) if b["url"]]
    assert len(urls) == 1 and urls[0]["text"] == f"💳 Оплатить {fmt_rub(get_plan_price('standard', 3))}"
    assert "Автопродление" not in screen.text
    assert not [b for b in _buttons(screen) if (b["data"] or "").startswith(("ps:", "ap:"))]
    rec = next(iter(f.store.payments.values()))
    assert rec.amount == get_plan_price("standard", 3)
    assert PayCheck(pid=rec.id).pack() in [b["data"] for b in _buttons(screen)]


async def test_old_pay_button_ignores_the_amount(money_flow):
    f = money_flow
    await f.press("pay_yookassa_pro_1_1")
    rec = next(iter(f.store.payments.values()))
    assert rec.plan_code == "pro" and rec.period_months == 1 and rec.amount == get_plan_price("pro", 1)
    assert f.container.payments.payments[rec.external_id]["amount"] == get_plan_price("pro", 1)


async def test_same_period_twice_reuses_the_pending_payment(money_flow):
    f = money_flow
    await f.press(Period(c="lite", m=1).pack())
    await f.press(Period(c="lite", m=1).pack())
    assert len(f.store.payments) == 1 and len(f.container.payments.payments) == 1


async def test_autorenew_line_and_toggle_when_flag_on(money_flow, monkeypatch):
    f = money_flow
    monkeypatch.setattr("app.config.settings.AUTOPAY_ENABLED", True)
    await f.press(Period(c="lite", m=1).pack())
    screen = _last_screen(f)
    assert "Автопродление: выключено" in screen.text
    assert AutoPay(a="on").pack() in [b["data"] for b in _buttons(screen)]
    await f.press(AutoPay(a="on").pack())
    screen = _last_screen(f)
    assert "Автопродление: включено" in screen.text
    saving = [p for p in f.container.payments.payments.values() if p["save_payment_method"]]
    assert len(saving) == 1


async def test_check_payment_pending_then_paid(money_flow):
    f = money_flow
    await f.press(Period(c="lite", m=1).pack())
    rec = next(iter(f.store.payments.values()))
    await f.press(PayCheck(pid=rec.id).pack())
    assert "Оплата пока не пришла" in _last_screen(f).text
    f.container.payments.succeed(rec.external_id)
    f.redis.store.clear()  # rate limit window
    await f.press(PayCheck(pid=rec.id).pack())
    assert "Оплата прошла, подписка активна" in _last_screen(f).text
    paid_msgs = [s for s in f.notifier.sent if s.kind == "user"]
    assert len(paid_msgs) == 1 and paid_msgs[0].reply_markup is not None
    assert len(f.container.provisioning.grants) == 1


async def test_legacy_check_payment_button_goes_to_new_check(money_flow):
    f = money_flow
    await f.press(Period(c="lite", m=1).pack())
    rec = next(iter(f.store.payments.values()))
    f.container.payments.succeed(rec.external_id)
    await f.press(f"check_payment:{rec.external_id}")
    assert "Оплата прошла" in _last_screen(f).text
    assert f.redis.store.get("legacy_hits:check_payment")


async def test_check_of_foreign_payment_is_not_found(money_flow):
    f = money_flow
    await f.press(Period(c="lite", m=1).pack())
    rec = next(iter(f.store.payments.values()))
    await f.press(PayCheck(pid=rec.id).pack(), u=user(uid=900000999))
    assert "Платеж не найден" in _last_screen(f).text


async def test_stars_disabled_answers_alert(money_flow):
    f = money_flow
    await f.press(PayStars(c="lite", m=1).pack())
    assert f.answers()[-1].params.get("show_alert")


def _pre_checkout(u, payload, amount):
    q = PreCheckoutQuery(id=str(next(_ids)), from_user=u, currency="XTR", total_amount=amount,
                         invoice_payload=payload)
    return Update(update_id=next(_ids), pre_checkout_query=q)


def _successful(u, payload, amount, charge):
    from datetime import datetime, timezone

    msg = Message(message_id=next(_ids), date=datetime.now(timezone.utc), chat=Chat(id=u.id, type="private"),
                  from_user=u, successful_payment=SuccessfulPayment(
                      currency="XTR", total_amount=amount, invoice_payload=payload,
                      telegram_payment_charge_id=charge, provider_payment_charge_id=""))
    return Update(update_id=next(_ids), message=msg)


async def test_stars_invoice_precheckout_and_successful_payment(money_flow, monkeypatch):
    f = money_flow
    monkeypatch.setattr("app.config.settings.STARS_ENABLED", True)
    monkeypatch.setattr("app.config.settings.STARS_RATE", 1.0)
    f.container.stars = __import__("tests.fakes.stars", fromlist=["FakeStarsGateway"]).FakeStarsGateway()
    from app.services.money import money

    m = money(f.container, store=f.store, hooks=FakeHooks(), clock=f.store.clock)
    await f.press(Period(c="lite", m=1).pack())
    assert any(b["data"] == PayStars(c="lite", m=1).pack() for b in _buttons(_last_screen(f)))
    await f.press(PayStars(c="lite", m=1).pack())
    invoice = f.container.stars.invoices[0]
    stars = invoice["stars"]
    assert stars == get_plan_price("lite", 1)

    await f.dp.feed_update(f.bot, _pre_checkout(f.user, invoice["payload"], stars - 1))
    await f.dp.feed_update(f.bot, _pre_checkout(f.user, invoice["payload"], stars))
    answers = f.session.calls_of("AnswerPreCheckoutQuery")
    assert [a.params["ok"] for a in answers] == [False, True]

    await f.dp.feed_update(f.bot, _successful(f.user, invoice["payload"], stars, "charge-77"))
    await f.dp.feed_update(f.bot, _successful(f.user, invoice["payload"], stars, "charge-77"))
    assert len(f.container.provisioning.grants) == 1
    rec = await m.deps.store.get(int(invoice["payload"].split(":")[1]))
    assert rec.status == "succeeded" and rec.telegram_charge_id == "charge-77" and rec.fulfilled


async def test_refund_request_flow_and_admin_decision(money_flow, monkeypatch):
    f = money_flow
    monkeypatch.setattr("app.config.settings.REFUND_24H_ENABLED", True)
    await f.press(Period(c="lite", m=1).pack())
    rec = next(iter(f.store.payments.values()))
    f.container.payments.succeed(rec.external_id)
    await f.money.fulfillment.process(rec.id, source="webhook")
    paid = [s for s in f.notifier.sent if s.kind == "user"][0]
    assert RefundReq(pid=rec.id).pack() in [b.callback_data for row in paid.reply_markup.inline_keyboard
                                            for b in row]
    await f.press(RefundReq(pid=rec.id).pack())
    assert any("Запрос на возврат принят" in (c.text or "") for c in f.session.calls_of("SendMessage"))
    admin_alert = [s for s in f.notifier.to_admins() if "Запрос на возврат" in s.text][0]
    rid = next(iter(f.store.requests))
    assert AdmRefund(a="ok", rid=rid).pack() in [b.callback_data for row in admin_alert.reply_markup.inline_keyboard
                                                 for b in row]

    await f.press(AdmRefund(a="ok", rid=rid).pack())  # not an admin
    assert f.answers()[-1].params.get("show_alert") and f.store.requests[rid].status == "pending"
    await f.press(AdmRefund(a="ok", rid=rid).pack(), u=user(uid=ADMIN))
    assert f.store.requests[rid].status == "refunded"
    assert f.container.payments.refunds and f.container.provisioning.revokes


async def test_old_review_buttons_reach_the_new_admin_handler(money_flow):
    f = money_flow
    await f.press(Period(c="pro", m=12).pack())
    rec = next(iter(f.store.payments.values()))
    f.container.payments.succeed(rec.external_id)
    f.container.payments.payments[rec.external_id]["amount"] = 1.0
    await f.money.fulfillment.process(rec.id, source="webhook")
    assert (await f.store.get(rec.id)).meta["needs_review"]
    await f.press(AdmReview(a="ok", pid=rec.id).pack())  # not an admin
    assert not f.container.provisioning.grants
    await f.press(f"rv_ok:{rec.id}", u=user(uid=ADMIN))
    assert len(f.container.provisioning.grants) == 1
    edited = f.session.calls_of("EditMessageText")[-1]
    assert "Одобрено" in edited.text


async def test_autopay_stop_button(money_flow):
    f = money_flow
    f.store.add_sub(f.user.id, autorenew=True)
    await f.press(AutoPay(a="stop").pack())
    assert (await f.store.main_subscription(f.user.id)).autorenew is False
    assert "Автопродление выключено" in f.session.calls_of("SendMessage")[-1].text


async def test_gifts_hidden_and_refused_when_flag_off(money_flow):
    f = money_flow
    await f.press(Nav(s="plans").pack())
    assert not [b for b in _buttons(_last_screen(f)) if (b["data"] or "").startswith("gf:")]
    await f.press("gf:buy:")
    assert f.answers()[-1].params.get("show_alert")


async def test_gift_purchase_flow_when_flag_on(money_flow, monkeypatch):
    f = money_flow
    monkeypatch.setattr("app.config.settings.GIFTS_ENABLED", True)
    await f.press(Nav(s="plans").pack())
    assert "gf:buy:" in [b["data"] for b in _buttons(_last_screen(f))]
    await f.press("gf:buy:")
    await f.press("gf:plan:standard")
    await f.press("gf:period:standard.3")
    rec = next(iter(f.store.payments.values()))
    assert rec.kind == "gift" and rec.amount == get_plan_price("standard", 3)
    assert "Подарок" in _last_screen(f).text


# --- obhod traffic packages (3.0 screen replacing the 2.x ScreenManager one) ----------------------

async def test_obhod_packages_screen_and_checkout(money_flow):
    from app.domain.plans import get_obhod_package

    f = money_flow
    await f.press(Nav(s="plans", p="obhod").pack())
    screen = _last_screen(f)
    datas = [b["data"] for b in _buttons(screen)]
    assert "pe:obhod_250:1" in datas and "pe:obhod_500:1" in datas and "n:plans:" in datas
    await f.press("pe:obhod_250:1")
    rec = next(iter(f.store.payments.values()))
    assert rec.kind == "obhod_package" and rec.plan_code == "obhod_250"
    assert rec.amount == get_obhod_package("obhod_250")["price"]
    screen = _last_screen(f)
    assert [b for b in _buttons(screen) if b["url"]]
    assert not [b for b in _buttons(screen) if (b["data"] or "").startswith(("ps:", "ap:"))]


async def test_obhod_package_refused_without_live_obhod(money_flow):
    f = money_flow
    f.money.deps.hooks.obhod_live = False
    await f.press("pe:obhod_500:1")
    assert not f.store.payments
    assert "только при активном тарифе Pro" in _last_screen(f).text


@pytest.mark.parametrize("legacy,kind", [
    ("ui:subscription_plans:obhod:-", "packages"),
    ("ui:subscription_plans:buy_obhod:obhod_250", "package_payment"),
    ("ui:subscription_plans:open:-", "plans"),
    ("ui:subscription_plans:extend:-", "plans"),
    ("ui:subscription_plan_detail:back:-", "plans"),
    ("ui:subscription_plans:select:pro", "periods"),
])
async def test_2x_plan_screen_buttons_land_on_3_0_screens(money_flow, legacy, kind):
    f = money_flow
    await f.press(legacy)
    datas = [b["data"] for b in _buttons(_last_screen(f))]
    if kind == "packages":
        assert "pe:obhod_250:1" in datas
    elif kind == "package_payment":
        assert next(iter(f.store.payments.values())).kind == "obhod_package"
    elif kind == "plans":
        assert "pl:pro" in datas
    else:
        assert "pe:pro:12" in datas


async def test_o3_legacy_select_period_opens_checkout_not_plans(money_flow):
    """O3: ui:subscription_plan_detail:select_period:<plan>_<months> used to fall through
    to the plans list; it must open checkout for that exact plan/period."""
    f = money_flow
    await f.press("ui:subscription_plan_detail:select_period:pro_3")
    rec = next(iter(f.store.payments.values()))
    assert rec.kind == "subscription" and rec.plan_code == "pro" and rec.period_months == 3
    screen = _last_screen(f)
    datas = [b["data"] for b in _buttons(screen)]
    assert not any((d or "").startswith("pe:") for d in datas)  # not the periods list either
    assert [b for b in _buttons(screen) if b["url"]]  # the pay button is there
    assert "Pro, 3" in screen.text


async def test_o3_legacy_select_period_bad_payload_falls_back_to_plans(money_flow):
    f = money_flow
    await f.press("ui:subscription_plan_detail:select_period:garbage")
    assert not f.store.payments
    assert "pl:pro" in [b["data"] for b in _buttons(_last_screen(f))]
