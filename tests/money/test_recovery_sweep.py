"""Recovery sweep (replaces the 2.x retry_needs_provisioning / recheck_pending_payments tests)."""
from datetime import timedelta

from app.domain.plans import get_plan_price
from tests.money.fakes import make_money

TG = 700005


async def _pending(m, deps, plan="lite"):
    res = await m.checkout.start_checkout(TG, await m.checkout.quote(TG, plan, 1))
    return res.intent


async def test_stale_pending_paid_payment_is_found_and_granted():
    m, deps = make_money()
    intent = await _pending(m, deps)
    deps.payments.succeed(intent.external_id)
    assert (await m.fulfillment.recover())["checked"] == 0  # younger than 15 minutes
    deps.clock.advance(minutes=16)
    stats = await m.fulfillment.recover()
    assert stats["fulfilled"] == 1 and len(deps.provisioning.grants) == 1


async def test_stale_pending_canceled_payment_is_closed():
    m, deps = make_money()
    intent = await _pending(m, deps)
    deps.payments.cancel(intent.external_id)
    deps.clock.advance(minutes=16)
    assert (await m.fulfillment.recover())["canceled"] == 1
    assert (await deps.store.get(intent.payment_id)).status == "canceled"


async def test_paid_but_not_granted_retried_with_flag_or_after_5_minutes():
    m, deps = make_money()
    deps.provisioning.fail_times = 1
    intent = await _pending(m, deps)
    deps.payments.succeed(intent.external_id)
    await m.fulfillment.process(intent.payment_id, source="webhook")  # fails -> needs_provisioning
    stats = await m.fulfillment.recover()
    assert stats["fulfilled"] == 1  # needs_provisioning: retried at once


async def test_recent_paid_without_flag_is_left_to_the_webhook():
    m, deps = make_money()
    rec = deps.store.add_payment(TG, status="succeeded", plan_code="lite", period_months=1,
                                 amount=get_plan_price("lite", 1), paid_at=deps.clock().replace(tzinfo=None),
                                 meta={"expected_amount": get_plan_price("lite", 1), "v3": True})
    assert (await m.fulfillment.recover())["checked"] == 0
    deps.clock.advance(minutes=6)
    assert (await m.fulfillment.recover())["fulfilled"] == 1
    assert (await deps.store.get(rec.id)).fulfilled


async def test_2x_paid_row_without_flag_is_never_regranted():
    """First 3.0 deploy: a 2.x succeeded row with subscription_id NULL and no
    needs_provisioning flag (2.x delivered it or decided not to) is not granted again."""
    m, deps = make_money()
    deps.store.add_payment(TG, status="succeeded", plan_code="lite", period_months=1,
                           amount=get_plan_price("lite", 1),
                           paid_at=deps.clock().replace(tzinfo=None) - timedelta(days=3),
                           meta={"expected_amount": get_plan_price("lite", 1)})
    deps.clock.advance(hours=1)
    assert (await m.fulfillment.recover())["checked"] == 0
    assert not deps.provisioning.grants


async def test_recovery_skips_payments_on_review():
    m, deps = make_money()
    deps.store.add_payment(TG, status="succeeded", plan_code="pro", period_months=12, amount=1,
                           meta={"needs_review": True, "review_reason": "сумма"},
                           created_at=deps.clock().replace(tzinfo=None) - timedelta(hours=2))
    deps.store.add_payment(TG, status="succeeded", plan_code="pro", period_months=12, amount=1,
                           meta={"needs_review": True, "review_rejected": True},
                           created_at=deps.clock().replace(tzinfo=None) - timedelta(hours=2))
    assert (await m.fulfillment.recover())["checked"] == 0
    assert not deps.provisioning.grants


async def test_already_granted_2x_rows_are_not_touched():
    m, deps = make_money()
    deps.store.add_payment(TG, status="succeeded", plan_code="pro", period_months=1, amount=449,
                           subscription_id=5, created_at=deps.clock().replace(tzinfo=None) - timedelta(hours=2))
    deps.store.add_payment(TG, status="succeeded", plan_code="obhod_250", kind="obhod_package", amount=599,
                           meta={"obhod_package_applied": True},
                           created_at=deps.clock().replace(tzinfo=None) - timedelta(hours=2))
    assert (await m.fulfillment.recover())["checked"] == 0
