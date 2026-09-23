"""24h refund requests: eligibility, one request per payment, idempotent admin decisions."""
from app.domain.models import AdminTopic
from app.services.fulfillment import Outcome
from tests.money.fakes import make_money

TG = 700003


async def _paid(m, deps, method="yookassa"):
    q = await m.checkout.quote(TG, "standard", 1)
    if method == "stars":
        res = await m.checkout.start_checkout(TG, q, method="stars")
        r = await m.fulfillment.on_stars_paid(telegram_id=TG, payment_id=res.intent.payment_id,
                                              charge_id="ch-1", total_amount=q.stars, currency="XTR")
    else:
        res = await m.checkout.start_checkout(TG, q)
        deps.payments.succeed(res.intent.external_id)
        r = await m.fulfillment.process(res.intent.payment_id, source="webhook")
    assert r.outcome is Outcome.FULFILLED
    return await deps.store.get(res.intent.payment_id)


async def test_request_needs_flag_owner_and_24h_window():
    m, deps = make_money(REFUND_24H_ENABLED=False)
    rec = await _paid(m, deps)
    assert await m.refunds.request(TG, rec.id) == "not_eligible"
    deps.settings.REFUND_24H_ENABLED = True
    assert await m.refunds.request(TG + 1, rec.id) == "not_eligible"
    deps.clock.advance(hours=25)
    assert await m.refunds.request(TG, rec.id) == "not_eligible"
    assert not deps.store.requests


async def test_one_request_per_payment_and_admin_gets_buttons():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    assert await m.refunds.request(TG, rec.id) == "requested"
    assert await m.refunds.request(TG, rec.id) == "already"
    alerts = deps.notifier.to_admins(AdminTopic.REFUNDS)
    assert len(alerts) == 1 and "не смог подключиться" in alerts[0].text


async def test_approve_refunds_money_revokes_access_once():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    deps.store.add_sub(TG, autorenew=True)
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    assert await m.refunds.decide(rid, 111, approve=True) == "done"
    assert deps.payments.refunds == [{"payment_id": rec.external_id, "amount": float(rec.amount),
                                      "key": f"rr:{rid}", "status": "succeeded"}]
    assert deps.provisioning.revokes == [(TG, f"refund_24h:{rid}")]
    assert (await deps.store.main_subscription(TG)).autorenew is False
    assert deps.store.requests[rid].status == "refunded"
    marker = (await deps.store.get(rec.id)).meta["refund_24h"]
    assert marker["state"] == "done" and marker["revoked"] is True
    assert (await m.refunds.decide(rid, 111, approve=True)).startswith("already:")
    assert (await m.refunds.decide(rid, 222, approve=False)).startswith("already:")
    assert len(deps.payments.refunds) == 1 and len(deps.provisioning.revokes) == 1
    assert any("Возврат одобрен" in s.text for s in deps.notifier.sent if s.kind == "user")


async def test_reject_tells_user_and_blocks_approval():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    assert await m.refunds.decide(rid, 111, approve=False) == "rejected"
    assert await m.refunds.decide(rid, 111, approve=True) == "already:rejected"
    assert not deps.payments.refunds and not deps.provisioning.revokes
    assert any("@crs_support" in s.text for s in deps.notifier.sent if s.kind == "user")


async def test_failed_money_refund_can_be_retried():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    real_refund = deps.payments.refund

    async def broken(*a, **kw):
        return None

    deps.payments.refund = broken
    assert (await m.refunds.decide(rid, 111, approve=True)).startswith("failed:")
    assert deps.store.requests[rid].status == "failed" and not deps.provisioning.revokes
    deps.payments.refund = real_refund
    assert await m.refunds.decide(rid, 111, approve=True) == "done"


async def test_revoke_failure_is_reported_to_admin():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    deps.provisioning.revoke_result = False
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    assert await m.refunds.decide(rid, 111, approve=True) == "done_no_revoke"


async def test_stars_refund_uses_refund_star_payment():
    m, deps = make_money(REFUND_24H_ENABLED=True, STARS_ENABLED=True, STARS_RATE=1.0)
    rec = await _paid(m, deps, method="stars")
    assert rec.telegram_charge_id == "ch-1"
    assert await m.refunds.request(TG, rec.id) == "requested"
    rid = next(iter(deps.store.requests))
    assert await m.refunds.decide(rid, 111, approve=True) == "done"
    assert deps.stars.refunds == [(TG, "ch-1")] and not deps.payments.refunds
    assert (await deps.store.get(rec.id)).status == "refunded"
    assert any("звезды вернулись" in s.text for s in deps.notifier.sent if s.kind == "user")


async def test_gift_and_autorenew_payments_are_not_eligible():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = deps.store.add_payment(TG, kind="gift", status="succeeded", plan_code="lite", period_months=1)
    assert await m.refunds.request(TG, rec.id) == "not_eligible"


# ----------------------------------------------------------------- review money M-1 / M-2


async def test_approve_takes_back_only_the_refunded_period():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    assert await m.refunds.decide(rid, 111, approve=True) == "done"
    assert deps.provisioning.revoke_months == [rec.period_months]


async def test_undelivered_payment_is_not_eligible_and_never_granted_after_refund():
    from app.services.payments.store import M_NEEDS_PROVISIONING, stuck_is_recoverable

    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = deps.store.add_payment(TG, kind="subscription", status="succeeded", plan_code="standard",
                                 period_months=1)
    await deps.store.patch_meta(rec.id, {M_NEEDS_PROVISIONING: True})
    assert await m.refunds.request(TG, rec.id) == "not_eligible"  # not delivered yet

    # Reverse race: a refund marker already on the row (approval in flight):
    # neither the recovery sweep nor a later process() grants it.
    await deps.store.patch_meta(rec.id, {"refund_24h": {"rid": 1, "state": "refunding"}})
    fresh = await deps.store.get(rec.id)
    assert not stuck_is_recoverable(fresh.meta, fresh.created_at, deps.clock().replace(tzinfo=None),
                                    __import__("datetime").timedelta(minutes=5))
    r = await m.fulfillment.process(rec.id, source="recovery")
    assert r.outcome is Outcome.REFUNDED
    assert not deps.provisioning.by_payment


async def test_approval_of_a_request_whose_payment_is_not_delivered_fails_safely():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    # simulate a row that lost its delivery marks (2.x data): approval refuses
    row = deps.store.payments[rec.id]
    row.subscription_id = None
    row.meta.pop("fulfilled_at", None)
    assert (await m.refunds.decide(rid, 111, approve=True)).startswith("failed:")
    assert not deps.payments.refunds and not deps.provisioning.revokes
