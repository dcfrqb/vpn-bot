"""Fulfillment state machine: price gate once, idempotent across webhook/check/recovery/review."""
from decimal import Decimal

import pytest

from app.domain.models import AdminTopic, EntitlementSource
from app.domain.plans import get_plan_price
from app.services.fulfillment import Outcome
from app.services.remna_tariff import RemnaUserDisabledError
from tests.money.fakes import make_money

TG = 700001


async def _paid_payment(m, deps, plan="pro", months=1, amount=None, kind="subscription", **meta):
    amount = get_plan_price(plan, months) if amount is None else amount
    q = await m.checkout.quote(TG, plan, months)
    res = await m.checkout.start_checkout(TG, q, kind=kind) if kind != "gift" else None
    if res is not None:
        rec = await deps.store.get(res.intent.payment_id)
    else:
        rec = deps.store.add_payment(TG, plan_code=plan, period_months=months, kind=kind, amount=amount,
                                     external_id="gift-ext", meta={"expected_amount": amount, **meta})
        deps.payments.payments["gift-ext"] = {"id": "gift-ext", "status": "pending", "amount": float(amount),
                                              "currency": "RUB", "metadata": {"tg_user_id": str(TG)}}
    deps.payments.succeed(rec.external_id)
    if amount != get_plan_price(plan, months):
        deps.payments.payments[rec.external_id]["amount"] = float(amount)
    return rec


async def test_paid_payment_is_granted_once_across_all_entry_points():
    m, deps = make_money()
    rec = await _paid_payment(m, deps)
    results = [
        await m.fulfillment.process_external(rec.external_id, source="webhook"),
        await m.fulfillment.process(rec.id, source="check"),
        await m.fulfillment.process(rec.id, source="recovery"),
        await m.fulfillment.process_external(rec.external_id, source="webhook"),
    ]
    assert [r.outcome for r in results] == [Outcome.FULFILLED, Outcome.ALREADY, Outcome.ALREADY, Outcome.ALREADY]
    assert len(deps.provisioning.grants) == 1
    tg, ent = deps.provisioning.grants[0]
    assert tg == TG and ent.plan_code == "pro" and ent.payment_id == rec.id
    assert ent.source is EntitlementSource.PAYMENT and ent.months == 1 and ent.days is None
    users = [s for s in deps.notifier.sent if s.kind == "user"]
    assert len(users) == 1 and "Оплата прошла" in users[0].text
    assert len(deps.notifier.to_admins(AdminTopic.PAYMENTS)) == 1
    after = await deps.store.get(rec.id)
    assert after.status == "succeeded" and after.fulfilled and after.meta["price_ok"] is True


async def test_pending_and_canceled_payments_grant_nothing():
    m, deps = make_money()
    q = await m.checkout.quote(TG, "lite", 1)
    res = await m.checkout.start_checkout(TG, q)
    assert (await m.fulfillment.process(res.intent.payment_id, source="check")).outcome is Outcome.PENDING
    deps.payments.cancel(res.intent.external_id)
    assert (await m.fulfillment.process(res.intent.payment_id, source="check")).outcome is Outcome.CANCELED
    assert (await deps.store.get(res.intent.payment_id)).status == "canceled"
    deps.payments.succeed(res.intent.external_id)  # canceled is terminal (2.x FSM)
    assert (await m.fulfillment.process(res.intent.payment_id, source="webhook")).outcome is Outcome.CANCELED
    assert not deps.provisioning.grants


async def test_provider_down_is_retry_and_grants_nothing():
    m, deps = make_money()
    rec = await _paid_payment(m, deps)

    async def down(_):
        return None

    deps.payments.get_payment = down
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.RETRY
    assert not deps.provisioning.grants


async def test_underpaid_payment_is_held_once_and_not_granted():
    m, deps = make_money()
    rec = await _paid_payment(m, deps, plan="pro", months=12, amount=1)
    for _ in range(3):
        r = await m.fulfillment.process(rec.id, source="webhook")
        assert r.outcome is Outcome.HELD
    assert not deps.provisioning.grants
    held = await deps.store.get(rec.id)
    assert held.meta["needs_review"] and "не совпадает" in held.meta["review_reason"]
    assert len(deps.notifier.to_admins(AdminTopic.PAYMENTS)) == 1  # one review alert
    assert len([s for s in deps.notifier.sent if s.kind == "user"]) == 1


async def test_review_approve_grants_and_second_click_is_noop():
    m, deps = make_money()
    rec = await _paid_payment(m, deps, plan="standard", months=3, amount=5)
    await m.fulfillment.process(rec.id, source="webhook")
    assert await m.fulfillment.decide_review(rec.id, 111, approve=True) == "approved"
    assert len(deps.provisioning.grants) == 1
    assert await m.fulfillment.decide_review(rec.id, 111, approve=True) == "already_done"
    assert await m.fulfillment.decide_review(rec.id, 111, approve=False) == "already_approved"
    assert len(deps.provisioning.grants) == 1


async def test_review_reject_blocks_later_grants():
    m, deps = make_money()
    rec = await _paid_payment(m, deps, plan="standard", months=3, amount=5)
    await m.fulfillment.process(rec.id, source="webhook")
    assert await m.fulfillment.decide_review(rec.id, 111, approve=False) == "rejected"
    assert (await m.fulfillment.process(rec.id, source="recovery")).outcome is Outcome.REJECTED
    assert await m.fulfillment.decide_review(rec.id, 111, approve=True) == "already_rejected"
    assert not deps.provisioning.grants


async def test_review_of_not_held_or_unknown_payment():
    m, deps = make_money()
    rec = await _paid_payment(m, deps)
    assert await m.fulfillment.decide_review(rec.id, 111, approve=True) == "not_held"
    assert await m.fulfillment.decide_review(99999, 111, approve=True) == "not_found"


async def test_grant_failure_is_retry_with_one_alert_then_recovers():
    m, deps = make_money()
    deps.provisioning.fail_times = 2
    rec = await _paid_payment(m, deps)
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.RETRY
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.RETRY
    stuck = await deps.store.get(rec.id)
    assert stuck.meta["needs_provisioning"] and not stuck.fulfilled
    alerts = deps.notifier.to_admins(AdminTopic.PAYMENTS)
    assert len(alerts) == 1 and "доступ не выдан" in alerts[0].text
    assert "panel down" not in "".join(s.text for s in deps.notifier.sent if s.kind == "user")
    assert (await m.fulfillment.process(rec.id, source="recovery")).outcome is Outcome.FULFILLED
    done = await deps.store.get(rec.id)
    assert done.fulfilled and "needs_provisioning" not in done.meta


async def test_disabled_panel_user_is_held_for_review():
    m, deps = make_money()
    deps.provisioning.fail_times = 1
    deps.provisioning.fail_with = RemnaUserDisabledError("disabled")
    rec = await _paid_payment(m, deps)
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.HELD
    assert "DISABLED" in (await deps.store.get(rec.id)).meta["review_reason"]
    assert await m.fulfillment.decide_review(rec.id, 111, approve=True) == "approved"


async def test_payer_mismatch_is_held():
    m, deps = make_money()
    rec = await _paid_payment(m, deps)
    deps.payments.payments[rec.external_id]["metadata"]["tg_user_id"] = "123"
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.HELD
    assert not deps.provisioning.grants


async def test_unknown_webhook_payment_is_adopted_and_price_checked():
    m, deps = make_money()
    deps.payments.payments["dash-1"] = {
        "id": "dash-1", "status": "succeeded", "amount": float(get_plan_price("lite", 1)), "currency": "RUB",
        "metadata": {"tg_user_id": str(TG), "plan_code": "lite", "period_months": "1"},
    }
    r = await m.fulfillment.process_external("dash-1", source="webhook")
    assert r.outcome is Outcome.FULFILLED
    assert (await deps.store.get_by_external("dash-1")).meta["adopted_from_webhook"]
    deps.payments.payments["dash-2"] = {"id": "dash-2", "status": "succeeded", "amount": 10.0, "metadata": {}}
    assert (await m.fulfillment.process_external("dash-2", source="webhook")).outcome is Outcome.NOT_FOUND


async def test_payment_without_plan_is_held_and_approval_uses_amount_fallback():
    m, deps = make_money()
    deps.payments.payments["dash-3"] = {
        "id": "dash-3", "status": "succeeded", "amount": 549.0, "currency": "RUB",
        "metadata": {"tg_user_id": str(TG)},
    }
    assert (await m.fulfillment.process_external("dash-3", source="webhook")).outcome is Outcome.HELD
    rec = await deps.store.get_by_external("dash-3")
    assert await m.fulfillment.decide_review(rec.id, 111, approve=True) == "approved"
    assert deps.provisioning.grants[0][1].plan_code == "premium"


async def test_obhod_package_payment_applies_package_once():
    m, deps = make_money()
    rec = deps.store.add_payment(TG, plan_code="obhod_250", period_months=1, kind="obhod_package",
                                 amount=get_plan_price("pro", 1), meta={"expected_amount": 599})
    deps.payments.payments[rec.external_id] = {"id": rec.external_id, "status": "succeeded", "amount": 599.0,
                                               "currency": "RUB", "metadata": {"tg_user_id": str(TG)}}
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.FULFILLED
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.ALREADY
    assert deps.hooks.obhod_calls == [(TG, "obhod_250", rec.id)] and not deps.provisioning.grants


async def test_obhod_package_not_applied_alerts_admin_once():
    m, deps = make_money()
    deps.hooks.obhod_ok = False
    rec = deps.store.add_payment(TG, plan_code="obhod_250", period_months=1, kind="obhod_package", amount=599,
                                 meta={"expected_amount": 599})
    deps.payments.payments[rec.external_id] = {"id": rec.external_id, "status": "succeeded", "amount": 599.0,
                                               "currency": "RUB", "metadata": {"tg_user_id": str(TG)}}
    await m.fulfillment.process(rec.id, source="webhook")
    await m.fulfillment.process(rec.id, source="recovery")
    manual = [s for s in deps.notifier.to_admins() if "НЕ применен" in s.text]
    assert len(manual) == 1


async def test_gift_payment_creates_code_and_sends_link():
    from tests.fakes.bot import make_bot

    bot, _ = make_bot()
    m, deps = make_money(bot=bot)
    rec = await _paid_payment(m, deps, plan="standard", months=3, kind="gift")
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.FULFILLED
    assert deps.promo.gifts == [(TG, "standard", 3, rec.id)] and not deps.provisioning.grants
    user = [s for s in deps.notifier.sent if s.kind == "user"][0]
    assert f"t.me/test_bot?start=g_GIFT{rec.id}" in user.text


async def test_gift_without_promo_support_waits_and_alerts():
    from tests.money.fakes import FakePromo

    m, deps = make_money(promo=FakePromo(with_gifts=False))
    rec = await _paid_payment(m, deps, plan="standard", months=3, kind="gift")
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.RETRY
    assert (await m.fulfillment.process(rec.id, source="recovery")).outcome is Outcome.RETRY
    assert len([s for s in deps.notifier.to_admins() if "Подарок оплачен" in s.text]) == 1
    assert len([s for s in deps.notifier.sent if s.kind == "user"]) == 1


async def test_blocked_card_alerts_admin_but_grants():
    m, deps = make_money()
    rec = await _paid_payment(m, deps)
    deps.payments.payments[rec.external_id]["card_fingerprint"] = "220000-1234-12/2030"
    deps.hooks.blocked_cards["220000-1234-12/2030"] = "мошенник"
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.FULFILLED
    assert any("стоп-листа" in s.text for s in deps.notifier.to_admins())


async def test_refunded_payment_is_never_granted():
    m, deps = make_money()
    rec = await _paid_payment(m, deps)
    await deps.store.set_status(rec.id, ("pending",), "refunded")
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.REFUNDED
    assert not deps.provisioning.grants


@pytest.mark.parametrize("flag,expect", [(False, False), (True, True)])
async def test_refund_button_only_with_flag(flag, expect):
    m, deps = make_money(REFUND_24H_ENABLED=flag)
    rec = await _paid_payment(m, deps)
    await m.fulfillment.process(rec.id, source="webhook")
    paid = await deps.store.get(rec.id)
    assert m.fulfillment.refund_button_allowed(paid) is expect
    deps.clock.advance(hours=25)
    assert m.fulfillment.refund_button_allowed(paid) is False


async def test_autorenew_checkout_saves_card_only_when_flag_on():
    m, deps = make_money(AUTOPAY_ENABLED=True)
    deps.store.add_sub(TG, valid_until=None)
    q = await m.checkout.quote(TG, "lite", 1)
    res = await m.checkout.start_checkout(TG, q, autorenew=True)
    assert res.intent.autorenew and deps.payments.payments[res.intent.external_id]["save_payment_method"]
    deps.payments.succeed(res.intent.external_id, payment_method_id="pm-1")
    await m.fulfillment.process(res.intent.payment_id, source="webhook")
    sub = await deps.store.main_subscription(TG)
    assert sub.autorenew and (await deps.store.get_method(sub.autorenew_method_id)).external_id == "pm-1"


async def test_amount_is_recorded_from_provider():
    m, deps = make_money()
    rec = await _paid_payment(m, deps, plan="lite", months=1)
    await m.fulfillment.process(rec.id, source="webhook")
    assert (await deps.store.get(rec.id)).amount == Decimal(get_plan_price("lite", 1))


async def test_grant_gets_calendar_months_and_approval_enables_disabled_user():
    from app.services.provisioning_rules import GrantRefused

    m, deps = make_money()
    deps.provisioning.fail_times = 1
    deps.provisioning.fail_with = GrantRefused("disabled", "panel user is DISABLED")
    rec = await _paid_payment(m, deps, plan="standard", months=6)
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.HELD
    assert deps.provisioning.calls[-1] == {"tg": TG, "months": 6, "enable_if_disabled": False}
    assert await m.fulfillment.decide_review(rec.id, 111, approve=True) == "approved"
    assert deps.provisioning.calls[-1] == {"tg": TG, "months": 6, "enable_if_disabled": True}


async def test_bad_plan_refusal_is_held_not_retried():
    from app.services.provisioning_rules import GrantRefused

    m, deps = make_money()
    deps.provisioning.fail_times = 5
    deps.provisioning.fail_with = GrantRefused("bad_plan", "unknown plan")
    rec = await _paid_payment(m, deps)
    assert (await m.fulfillment.process(rec.id, source="webhook")).outcome is Outcome.HELD
    assert "bad_plan" in (await deps.store.get(rec.id)).meta["review_reason"]


async def test_old_2x_row_is_not_announced_again():
    """Review money m-1: an old «Проверить оплату» or a YooKassa re-delivery on
    a 2.x row (no v3 marker, no notified marks) sends nothing."""
    m, deps = make_money()
    rec = deps.store.add_payment(700777, kind="subscription", status="succeeded", plan_code="lite",
                                 period_months=1, subscription_id=42, meta={"plan_code": "lite"})
    r = await m.fulfillment.process(rec.id, source="check")
    assert r.outcome is Outcome.ALREADY
    assert not deps.notifier.sent


async def test_payment_lock_is_released_only_by_its_owner(monkeypatch):
    """Review money m-5: a lock another process took after our TTL ran out is
    not deleted by our release."""
    from tests.fakes.redis import FakeRedis

    redis = FakeRedis()
    monkeypatch.setattr("app.services.cache.get_redis_client", lambda: redis)
    m, deps = make_money()
    ff = m.fulfillment
    assert await ff._lock("ext-9") is True
    assert await ff._lock("ext-9") is False
    redis.store["provision_lock:ext-9"] = "someone-else"  # our TTL expired, another process took it
    await ff._unlock("ext-9")
    assert redis.store.get("provision_lock:ext-9") == "someone-else"
