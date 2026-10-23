"""Durable access recovery with real SQL/journal and a simulated panel only."""
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.domain.models import Entitlement, EntitlementSource
from tests.integration.test_money_real_postgres import _cleanup

PG_URL = os.getenv("HOTFIX_PG_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL is not set")]


class _Bypass:
    def __init__(self):
        self.fail = False
        self.confirmations = 0

    async def on_main_granted(self, *args):
        pass

    async def on_main_revoked(self, *args):
        pass

    async def on_main_refunded(self, *args):
        self.confirmations += 1
        return not self.fail


class _ProvisioningProxy:
    def __init__(self, actual, fail):
        self.actual, self.fail = actual, fail

    def __getattr__(self, name):
        return getattr(self.actual, name)

    async def rollback(self, *args, **kwargs):
        from app.services.provisioning import ProvisioningError

        if self.fail:
            raise ProvisioningError("simulated panel unavailable")
        return await self.actual.rollback(*args, **kwargs)

    async def revoke(self, *args, **kwargs):
        from app.services.provisioning import ProvisioningError

        if self.fail:
            raise ProvisioningError("simulated panel unavailable")
        return await self.actual.revoke(*args, **kwargs)


@pytest.fixture
async def harness():
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import RemnaUser
    from app.services.accounts import SqlAccountsRepo
    from app.services.money import MoneyDeps, build_money
    from app.services.payments.sql_store import SqlPaymentStore
    from app.services.payments.ui import NullUi
    from app.services.provisioning import PanelProvisioningService
    from tests.fakes.notifier import RecordingNotifier
    from tests.fakes.payments import FakePaymentGateway
    from tests.fakes.redis import FakeRedis
    from tests.fakes.remnawave import FakeRemnaGateway
    from tests.fakes.stars import FakeStarsGateway
    from tests.money.fakes import Clock, FakeHooks, FakePromo, Settings

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tg = 750000000 + uuid.uuid4().int % 1000000
    clock = Clock(datetime(2026, 10, 22, 12, tzinfo=timezone.utc))
    gw = FakeRemnaGateway()
    gw.fake.now = clock()
    gw.fake._next_id = 300000 + uuid.uuid4().int % 100000
    payments, notifier, bypass = FakePaymentGateway(), RecordingNotifier(), _Bypass()
    settings = Settings(REFUND_24H_ENABLED=True)

    def restart(fail=False):
        actual = PanelProvisioningService(
            gw, SqlAccountsRepo(Session), notifier=notifier, obhod=bypass,
            clock=clock, settings=settings, late_patch_delay_s=0,
        )
        return build_money(MoneyDeps(
            payments=payments, stars=FakeStarsGateway(), provisioning=_ProvisioningProxy(actual, fail),
            notifier=notifier, promo=FakePromo(), store=SqlPaymentStore(Session), settings=settings,
            ui=NullUi(), hooks=FakeHooks(), clock=clock,
        ))

    class Harness:
        pass

    h = Harness()
    h.Session, h.tg, h.clock = Session, tg, clock
    h.gw, h.payments, h.notifier, h.bypass, h.restart = gw, payments, notifier, bypass, restart
    try:
        with patch("app.services.cache.get_redis_client", return_value=FakeRedis()):
            yield h
    finally:
        await _cleanup(Session, tg)
        async with Session() as session:
            for uid in gw.fake.users:
                await session.execute(delete(RemnaUser).where(RemnaUser.remna_id == str(uid)))
            await session.commit()
        await engine.dispose()


async def _paid_with_previous_rights(h):
    from app.services.fulfillment import Outcome

    m = h.restart()
    await m.deps.provisioning.grant(h.tg, Entitlement(
        plan_code="lite", source=EntitlementSource.GIFT, days=30,
    ), trace_id="prior-gift")
    uid = h.gw.fake.created[0]["id"]
    baseline = h.gw.fake.users[uid]["expireAt"]
    q = await m.checkout.quote(h.tg, "pro", 1)
    res = await m.checkout.start_checkout(h.tg, q)
    h.payments.succeed(res.intent.external_id)
    assert (await m.fulfillment.process(res.intent.payment_id, source="webhook")).outcome is Outcome.FULFILLED
    rec = await m.deps.store.get(res.intent.payment_id)
    assert await m.refunds.request(h.tg, rec.id) == "requested"
    rr = await m.deps.store.refund_request_for_payment(rec.id)
    return m, rec, rr, uid, baseline


@pytest.mark.parametrize("phase", ["before_patch", "bypass_confirmation"])
@pytest.mark.parametrize("later_grant", [False, True])
async def test_access_retry_after_restart_without_second_money_refund(harness, phase, later_grant):
    h = harness
    m, rec, rr, uid, baseline = await _paid_with_previous_rights(h)
    original = h.gw.fake.users[uid]["expireAt"]
    h.bypass.fail = phase == "bypass_confirmation"
    await h.restart(fail=phase == "before_patch").refunds.decide(rr.id, 111, approve=True)
    assert len(h.payments.refunds) == 1
    after_failure = h.gw.fake.users[uid]["expireAt"]
    assert datetime.fromisoformat(after_failure.replace("Z", "+00:00")) == datetime.fromisoformat(
        (original if phase == "before_patch" else baseline).replace("Z", "+00:00"))
    h.bypass.fail = False
    if later_grant:
        await h.restart().deps.provisioning.grant(h.tg, Entitlement(
            plan_code="standard", source=EntitlementSource.ADMIN, days=5,
        ), trace_id="later-compensation")
    h.clock.advance(hours=1)
    resumed = h.restart()
    await resumed.refunds.recover_access()
    row = await resumed.deps.store.get(rec.id)
    assert row.meta["refund_24h"]["access"]["state"] == "applied"
    assert len(h.payments.refunds) == 1
    actual = datetime.fromisoformat(h.gw.fake.users[uid]["expireAt"].replace("Z", "+00:00"))
    expected = datetime.fromisoformat(baseline.replace("Z", "+00:00"))
    if later_grant:
        from datetime import timedelta

        expected += timedelta(days=5)
    assert actual == expected
    assert h.gw.fake.squad_names(uid) == ["standard" if later_grant else "lite"]
    patches = len(h.gw.fake.patches)
    await h.restart().refunds.recover_access()
    assert len(h.gw.fake.patches) == patches
    assert len(h.payments.refunds) == 1


async def test_sql_claim_is_exclusive_reclaimable_and_owner_checked(harness):
    import asyncio
    from datetime import timedelta

    h = harness
    _, rec, rr, _, _ = await _paid_with_previous_rights(h)
    await h.restart(fail=True).refunds.decide(rr.id, 111, approve=True)
    # Refund recovery must survive the ordinary payment recovery horizon.
    h.clock.advance(days=60)
    a, b = h.restart().deps.store, h.restart().deps.store
    assert rec.id in [item.id for item in await a.refund_access_candidates(h.clock(), limit=20)]
    claimed = await asyncio.gather(*[
        store.claim_refund_access(rec.id, owner=owner, now=h.clock(), lease_for=timedelta(minutes=2))
        for store, owner in [(a, "worker-a"), (b, "worker-b")]
    ])
    assert sum(item is not None for item in claimed) == 1
    assert await a.finish_refund_access(rec.id, owner="unrelated", state="manual_review", now=h.clock()) is None
    h.clock.advance(minutes=3)
    assert await b.claim_refund_access(rec.id, owner="restarted-worker", now=h.clock(),
                                       lease_for=timedelta(minutes=2)) is not None
    assert await a.finish_refund_access(rec.id, owner="worker-a", state="applied", now=h.clock()) is None
    assert await b.finish_refund_access(rec.id, owner="restarted-worker", state="manual_review", now=h.clock()) is not None
    h.clock.advance(days=60)
    patches = len(h.gw.fake.patches)
    await h.restart().refunds.recover_access()
    assert len(h.gw.fake.patches) == patches
    candidates = await h.restart().deps.store.refund_access_candidates(h.clock(), limit=20)
    assert rec.id not in [item.id for item in candidates]
    assert len(h.payments.refunds) == 1


async def test_completed_access_retries_only_notice_after_notification_failure(harness, monkeypatch):
    h = harness
    _, rec, rr, uid, _ = await _paid_with_previous_rights(h)
    delivered = h.notifier.notify_user

    async def unavailable(*args, **kwargs):
        raise OSError("simulated Telegram unavailable")

    monkeypatch.setattr(h.notifier, "notify_user", unavailable)
    await h.restart().refunds.decide(rr.id, 111, approve=True)
    completed = await h.restart().deps.store.get(rec.id)
    assert completed.meta["refund_24h"]["access"]["state"] == "applied"
    patches = len(h.gw.fake.patches)
    expiry = h.gw.fake.users[uid]["expireAt"]
    monkeypatch.setattr(h.notifier, "notify_user", delivered)
    h.clock.advance(hours=1)
    await h.restart().refunds.recover_access()
    assert len(h.gw.fake.patches) == patches
    assert h.gw.fake.users[uid]["expireAt"] == expiry
    assert len(h.payments.refunds) == 1
    messages = [s for s in h.notifier.sent if s.kind == "user" and "Из срока подписки снято" in s.text]
    assert len(messages) == 1
    await h.restart().refunds.recover_access()
    assert len([s for s in h.notifier.sent if s.kind == "user" and "Из срока подписки снято" in s.text]) == 1


@pytest.mark.parametrize("confirmation_order", ["during_lease", "after_manual_result"])
async def test_new_money_confirmation_wins_over_stale_unconfirmed_result(harness, confirmation_order):
    from datetime import timedelta

    h = harness
    m, rec, rr, uid, baseline = await _paid_with_previous_rights(h)
    store = m.deps.store
    await store.transition_refund_request(rr.id, ("pending",), "approved", decided_by=111)
    await store.update_refund_marker(rec.id, {"rid": rr.id, "state": "refunding", "money_status": "submitting"},
                                     access_patch={"state": "awaiting_money"})
    assert await store.claim_refund_access(rec.id, owner="old-worker", now=h.clock(),
                                           lease_for=timedelta(minutes=10)) is not None
    await h.payments.refund(rec.external_id, idempotence_key=f"rr:{rr.id}")

    async def confirm():
        # A successful direct provider response publishes both money and access
        # intent, just as RefundRequests.decide does after its awaited call.
        await h.restart().deps.store.update_refund_marker(rec.id, {"money_status": "succeeded", "state": "done"},
            access_patch={"state": "pending", "due_at": h.clock().replace(tzinfo=None).isoformat()})

    async def stale_finish():
        await h.restart().deps.store.finish_refund_access(
            rec.id, owner="old-worker", state="manual_review", now=h.clock(),
            reason_class="money_unconfirmed", error="read before confirmation")

    if confirmation_order == "during_lease":
        await confirm()
        await stale_finish()
    else:
        await stale_finish()
        await confirm()
    latest = await h.restart().deps.store.get(rec.id)
    assert latest.meta["refund_24h"]["access"]["state"] != "manual_review"
    h.clock.advance(hours=1)
    await h.restart().refunds.recover_access()
    assert (await store.get(rec.id)).meta["refund_24h"]["access"]["state"] == "applied"
    assert datetime.fromisoformat(h.gw.fake.users[uid]["expireAt"].replace("Z", "+00:00")) == datetime.fromisoformat(
        baseline.replace("Z", "+00:00"))
    assert len(h.payments.refunds) == 1


async def test_due_access_is_prioritized_over_twenty_old_notice_failures(harness):
    from decimal import Decimal

    h = harness
    store = h.restart().deps.store
    for index in range(20):
        await store.create(h.tg, provider="yookassa", external_id=f"notice-{uuid.uuid4().hex}",
            amount=Decimal(129), currency="RUB", status="refunded", plan_code="lite", months=1,
            kind="subscription", method="yookassa", description="Old test notice", meta={"refund_24h": {
                "rid": 10000 + index, "state": "done", "money_status": "succeeded", "revoked": True,
                "access": {"state": "applied", "notice_phase": "applied", "notice_pending": True,
                           "notice_user_delivered": False, "notice_admin_delivered": True},
            }})
    _, rec, rr, _, _ = await _paid_with_previous_rights(h)
    await h.restart(fail=True).refunds.decide(rr.id, 111, approve=True)
    h.clock.advance(hours=1)
    selected = await h.restart().deps.store.refund_access_candidates(h.clock(), limit=20)
    assert selected[0].id == rec.id
