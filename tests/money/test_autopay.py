"""Autopay: off by flag, notice once, one charge per period, off after 2 failures."""
from datetime import timedelta

from app.domain.models import EntitlementSource, PaymentStatus
from app.domain.plans import get_plan_price
from tests.money.fakes import make_money

TG = 700004


def _setup(m, deps, *, days_left: float):
    now = deps.clock().replace(tzinfo=None)
    sub = deps.store.add_sub(TG, plan_code="standard", valid_until=now + timedelta(days=days_left),
                             autorenew=True)
    mid = len(deps.store.methods) + 1
    deps.store.methods[mid] = __import__("app.services.payments.store", fromlist=["SavedMethodRecord"]) \
        .SavedMethodRecord(id=mid, telegram_id=TG, provider="yookassa", external_id="pm-9")
    sub.autorenew_method_id = mid
    deps.store.add_payment(TG, status="succeeded", plan_code="standard", period_months=3, kind="subscription",
                           amount=get_plan_price("standard", 3), meta={"fulfilled_at": "x"})
    return sub


async def test_flag_off_does_nothing():
    m, deps = make_money(AUTOPAY_ENABLED=False)
    _setup(m, deps, days_left=0.5)
    assert await m.autopay.run_once() == {"notices": 0, "charged": 0, "failed": 0, "skipped": 0}
    assert not deps.payments.charges


async def test_notice_three_days_before_is_sent_once():
    m, deps = make_money(AUTOPAY_ENABLED=True)
    _setup(m, deps, days_left=2.5)
    await m.autopay.run_once()
    await m.autopay.run_once()
    notices = [s for s in deps.notifier.sent if s.kind == "user"]
    assert len(notices) == 1 and "Через 3 дня спишем" in notices[0].text
    assert not deps.payments.charges


async def test_charge_one_day_before_once_per_period_at_catalog_price():
    m, deps = make_money(AUTOPAY_ENABLED=True)
    _setup(m, deps, days_left=0.5)

    orig = deps.payments.charge_saved_method

    async def charge_and_succeed(intent, **kw):
        res = await orig(intent, **kw)
        deps.payments.succeed(res.external_id)
        return res

    deps.payments.charge_saved_method = charge_and_succeed
    stats = await m.autopay.run_once()
    assert stats["charged"] == 1
    assert deps.payments.charges == [{"method": "pm-9", "key": deps.payments.charges[0]["key"],
                                      "amount": get_plan_price("standard", 3)}]
    assert deps.payments.charges[0]["key"].startswith("autopay:")
    _, ent = deps.provisioning.grants[0]
    assert ent.source is EntitlementSource.AUTORENEW and ent.plan_code == "standard"
    await m.autopay.run_once()  # period moved forward: nothing to charge
    assert len(deps.payments.charges) == 1


async def test_failed_charge_retried_after_12h_then_turned_off():
    m, deps = make_money(AUTOPAY_ENABLED=True)
    _setup(m, deps, days_left=0.9)
    orig = deps.payments.charge_saved_method

    async def charge_and_decline(intent, **kw):
        from dataclasses import replace

        res = await orig(intent, **kw)
        deps.payments.cancel(res.external_id)
        return replace(res, status=PaymentStatus.CANCELED)

    deps.payments.charge_saved_method = charge_and_decline
    await m.autopay.run_once()
    assert len(deps.payments.charges) == 1
    await m.autopay.run_once()  # too early for a retry
    assert len(deps.payments.charges) == 1
    deps.clock.advance(hours=13)
    await m.autopay.run_once()
    assert len(deps.payments.charges) == 2
    assert (await deps.store.main_subscription(TG)).autorenew is False
    texts = [s.text for s in deps.notifier.sent if s.kind == "user"]
    assert any("Не получилось списать" in t for t in texts) and any("Автопродление выключено" in t for t in texts)
    assert not deps.provisioning.grants


async def test_no_late_charge_for_long_expired_subscription():
    m, deps = make_money(AUTOPAY_ENABLED=True)
    _setup(m, deps, days_left=-2)
    await m.autopay.run_once()
    assert not deps.payments.charges


async def test_user_can_stop_autorenew():
    m, deps = make_money(AUTOPAY_ENABLED=True)
    _setup(m, deps, days_left=2.5)
    was_on, until = await m.autopay.stop(TG)
    assert was_on and until is not None
    assert (await m.autopay.stop(TG))[0] is False
    await m.autopay.run_once()
    assert not deps.notifier.sent and not deps.payments.charges
