"""Stream A on real Postgres: SqlPaymentStore + Fulfillment + stream B provisioning.

Run: HOTFIX_PG_URL=postgresql+asyncpg://u:p@127.0.0.1:55432/crs pytest -m integration \
     tests/integration/test_money_real_postgres.py
The database must be at alembic head (r30_01+: payments.plan_code/kind/method,
refund_requests, payment_methods, subscriptions.autorenew).
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from dateutil.relativedelta import relativedelta

pytestmark = [pytest.mark.integration]
PG_URL = os.getenv("HOTFIX_PG_URL")


class _NoObhod:
    async def on_main_granted(self, *a):
        pass

    async def on_main_revoked(self, *a):
        pass


async def _cleanup(Session, tg):
    from sqlalchemy import delete

    from app.db.models import Payment, RefundRequest, SavedPaymentMethod, Subscription, TelegramUser

    async with Session() as s:
        await s.execute(delete(RefundRequest).where(RefundRequest.telegram_user_id == tg))
        await s.execute(delete(Payment).where(Payment.telegram_user_id == tg))
        await s.execute(delete(SavedPaymentMethod).where(SavedPaymentMethod.telegram_user_id == tg))
        await s.execute(delete(Subscription).where(Subscription.telegram_user_id == tg))
        await s.execute(delete(TelegramUser).where(TelegramUser.telegram_id == tg))
        await s.commit()


@pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL is not set")
async def test_paid_payment_stacks_on_trial_once_through_fulfillment():
    """B1 + B2 end to end: first payment of a user whose panel account exists (trial)
    but whose id is not stored; the month is added to the trial end, once."""
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Payment, RemnaUser, Subscription, TelegramUser
    from app.services.accounts import SqlAccountsRepo
    from app.services.fulfillment import Outcome
    from app.services.money import MoneyDeps, build_money
    from app.services.payments.sql_store import SqlPaymentStore
    from app.services.payments.ui import NullUi
    from app.services.provisioning import PanelProvisioningService
    from tests.fakes.notifier import RecordingNotifier
    from tests.fakes.payments import FakePaymentGateway
    from tests.fakes.redis import FakeRedis
    from tests.fakes.remnawave import FakeRemnaGateway
    from tests.fakes.stars import FakeStarsGateway
    from tests.money.fakes import FakeHooks, FakePromo, Settings

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tg = 730000000 + (uuid.uuid4().int % 1000000)
    panel_id = 6000 + (uuid.uuid4().int % 100000)
    trial_end = (datetime.now(timezone.utc) + timedelta(days=4)).replace(microsecond=0)
    gw = FakeRemnaGateway()
    gw.fake.add_user(panel_id, f"tg_{tg}", telegram_id=tg, squads=["standard"], limit=5,
                     expire=trial_end.strftime("%Y-%m-%dT%H:%M:%SZ"))
    store = SqlPaymentStore(Session)
    notifier = RecordingNotifier()
    prov = PanelProvisioningService(gw, SqlAccountsRepo(Session), obhod=_NoObhod(), settings=SimpleNamespace(),
                                    late_patch_delay_s=0)
    deps = MoneyDeps(payments=FakePaymentGateway(), stars=FakeStarsGateway(), provisioning=prov, notifier=notifier,
                     promo=FakePromo(), store=store, settings=Settings(), ui=NullUi(), hooks=FakeHooks())
    m = build_money(deps)
    try:
        with patch("app.services.cache.get_redis_client", return_value=FakeRedis()):
            q = await m.checkout.quote(tg, "standard", 1)
            res = await m.checkout.start_checkout(tg, q, user={"username": "it", "first_name": "IT"})
            assert res.ok
            again = await m.checkout.start_checkout(tg, q)
            assert again.reused and again.intent.payment_id == res.intent.payment_id
            deps.payments.succeed(res.intent.external_id)
            first = await m.fulfillment.process_external(res.intent.external_id, source="webhook")
            second = await m.fulfillment.process(res.intent.payment_id, source="check")
        assert first.outcome is Outcome.FULFILLED, first.detail
        assert second.outcome is Outcome.ALREADY

        async with Session() as s:
            sub = (await s.execute(select(Subscription).where(
                Subscription.telegram_user_id == tg, Subscription.sub_kind == "main"))).scalar_one()
            user = (await s.execute(select(TelegramUser).where(TelegramUser.telegram_id == tg))).scalar_one()
            pay = (await s.execute(select(Payment).where(Payment.id == res.intent.payment_id))).scalar_one()
        assert sub.active and sub.provisioning_state == "synced"
        assert user.remna_user_id == str(panel_id) and user.username == "it"
        expected = trial_end + relativedelta(months=1)
        assert abs((sub.valid_until.replace(tzinfo=timezone.utc) - expected).total_seconds()) < 5
        assert pay.status == "succeeded" and pay.subscription_id == sub.id
        assert pay.plan_code == "standard" and pay.period_months == 1 and pay.kind == "subscription"
        assert pay.method == "yookassa" and pay.payment_metadata.get("fulfilled_at")
        assert len([x for x in notifier.sent if x.kind == "user"]) == 1
        assert len(gw.fake.created) == 0  # the existing account was used, not a new one
    finally:
        await _cleanup(Session, tg)
        async with Session() as s:
            await s.execute(delete(RemnaUser).where(RemnaUser.remna_id == str(panel_id)))
            await s.commit()
        await engine.dispose()


@pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL is not set")
async def test_sql_store_state_changes_are_compare_and_set():
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Subscription, TelegramUser
    from app.services.payments.sql_store import SqlPaymentStore

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tg = 740000000 + (uuid.uuid4().int % 1000000)
    store = SqlPaymentStore(Session)
    now = datetime.now(timezone.utc)
    try:
        rec = await store.create(tg, provider="yookassa", external_id=f"it-{tg}", amount=Decimal(129),
                                 currency="RUB", status="pending", plan_code="lite", months=1, kind="subscription",
                                 method="yookassa", description="d",
                                 meta={"confirmation_url": "https://x", "expected_amount": 129})
        dup = await store.create(tg, provider="yookassa", external_id=f"it-{tg}", amount=Decimal(129),
                                 currency="RUB", status="pending", plan_code="lite", months=1, kind="subscription",
                                 method="yookassa", description="d", meta={})
        assert dup.id == rec.id  # same provider payment -> same row
        assert (await store.find_reusable(tg, plan_code="lite", months=1, kind="subscription", method="yookassa",
                                          autorenew=False, since=now - timedelta(minutes=15))).id == rec.id
        assert await store.patch_meta(rec.id, {}, claim="notified") is True
        assert await store.patch_meta(rec.id, {}, claim="notified") is False
        paid = await store.mark_paid(rec.id, amount=Decimal(129), card_fingerprint="220000-1234-12/2030")
        assert paid.status == "succeeded" and paid.paid_at is not None
        assert await store.set_status(rec.id, ("pending",), "canceled") is False
        count, total = await store.payer_stats(tg)
        assert count == 1 and total == Decimal("129.00")

        rr, created = await store.create_refund_request(rec.id, tg, amount=Decimal(129), reason="not_connected")
        rr2, created2 = await store.create_refund_request(rec.id, tg, amount=Decimal(129), reason="x")
        assert created and not created2 and rr2.id == rr.id
        assert (await store.transition_refund_request(rr.id, ("pending",), "approved", decided_by=1)).status == "approved"
        assert await store.transition_refund_request(rr.id, ("pending",), "rejected") is None

        async with Session() as s:
            await s.execute(pg_insert(TelegramUser).values(telegram_id=tg).on_conflict_do_nothing())
            s.add(Subscription(telegram_user_id=tg, plan_code="lite", active=True, sub_kind="main",
                               valid_until=(now + timedelta(hours=10)).replace(tzinfo=None)))
            await s.commit()
        mid = await store.save_method(tg, provider="yookassa", external_id=f"pm-{tg}", title="Карта *1234",
                                      card_fingerprint=None)
        assert await store.save_method(tg, provider="yookassa", external_id=f"pm-{tg}", title="t",
                                       card_fingerprint=None) == mid
        assert await store.set_autorenew(tg, True, method_id=mid)
        subs = await store.autorenew_subscriptions(now + timedelta(days=1))
        assert [s.telegram_id for s in subs if s.telegram_id == tg] == [tg]
        await store.mark_fulfilled(rec.id)
        after = await store.get(rec.id)
        assert after.fulfilled and after.subscription_id is not None
        assert (await store.last_paid_subscription(tg)).id == rec.id
        pending, stuck = await store.recovery_candidates(now, pending_age=timedelta(minutes=15),
                                                         stuck_age=timedelta(minutes=5), horizon=timedelta(days=30),
                                                         limit=20)
        assert rec.id not in [p.id for p in pending + stuck]
        async with Session() as s:
            row = (await s.execute(select(Subscription).where(Subscription.telegram_user_id == tg))).scalar_one()
        assert row.autorenew is True and row.autorenew_method_id == mid
    finally:
        await _cleanup(Session, tg)
        await engine.dispose()


@pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL is not set")
async def test_refunded_status_can_be_recorded():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services.payments.sql_store import SqlPaymentStore

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tg = 750000000 + (uuid.uuid4().int % 1000000)
    store = SqlPaymentStore(Session)
    try:
        rec = await store.create(tg, provider="stars", external_id=f"stars:it-{tg}", amount=Decimal(90),
                                 currency="XTR", status="pending", plan_code="lite", months=1, kind="subscription",
                                 method="stars", description="d", meta={"expected_stars": 90})
        await store.mark_paid(rec.id, amount=Decimal(90), charge_id="ch")
        assert await store.set_status(rec.id, ("succeeded",), "refunded")
    finally:
        await _cleanup(Session, tg)
        await engine.dispose()


@pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL is not set")
async def test_recovery_window_skips_delivered_gifts_and_packages_and_refunds():
    """Review money m-4 / M-2 on real Postgres: the JSON filters of
    recovery_candidates keep delivered gifts/packages and 24h-refunded rows out."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services.payments.sql_store import SqlPaymentStore

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tg = 760000000 + (uuid.uuid4().int % 1000000)
    store = SqlPaymentStore(Session)
    now = datetime.now(timezone.utc)
    ids = {}
    try:
        for name, meta in (("gift", {"fulfilled_at": now.isoformat(), "gift_code": "g_x"}),
                           ("package", {"obhod_package_applied": True}),
                           ("refunded", {"needs_provisioning": True, "refund_24h": {"rid": 1}}),
                           ("stuck", {"needs_provisioning": True})):
            rec = await store.create(tg, provider="yookassa", external_id=f"it-{name}-{tg}", amount=Decimal(129),
                                     currency="RUB", status="pending", plan_code="lite", months=1,
                                     kind="subscription", method="yookassa", description="d", meta=meta)
            await store.mark_paid(rec.id, amount=Decimal(129))
            ids[name] = rec.id
        _, stuck = await store.recovery_candidates(now + timedelta(hours=1), pending_age=timedelta(minutes=15),
                                                   stuck_age=timedelta(minutes=5), horizon=timedelta(days=30),
                                                   limit=100)
        got = {r.id for r in stuck} & set(ids.values())
        assert got == {ids["stuck"]}
    finally:
        await _cleanup(Session, tg)
        await engine.dispose()


@pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL is not set")
async def test_r30_03_kind_backfill_labels_2x_obhod_packages():
    """Review money m-2: the r30_03 kind backfill, run on real rows."""
    import re
    from pathlib import Path

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    src = Path(__file__).resolve().parents[2] / "src/app/db/migrations/versions/r30_03_data.py"
    body = src.read_text()
    m = re.search(r'"""\s*(UPDATE payments\s+SET kind = CASE.*?WHERE kind IS NULL)\s*"""', body, re.S)
    sql = m.group(1).encode().decode("unicode_escape")  # the file's "\\_" is "\_" at runtime
    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tg = 770000000 + (uuid.uuid4().int % 1000000)
    try:
        async with Session() as s:
            await s.execute(text("INSERT INTO telegram_users (telegram_id) VALUES (:t) ON CONFLICT DO NOTHING"),
                            {"t": tg})
            for ext, provider, plan in ((f"a{tg}", "yookassa", "obhod_250"), (f"b{tg}", "yookassa", "pro"),
                                        (f"c{tg}", "promo", "trial"), (f"d{tg}", "yookassa", "obhodx")):
                await s.execute(text(
                    "INSERT INTO payments (telegram_user_id, provider, external_id, amount, currency, status, "
                    "payment_metadata, created_at, updated_at, paid_at) VALUES (:t, :p, :e, 1, 'RUB', 'succeeded', "
                    "CAST(:m AS json), now(), now(), now())"),
                    {"t": tg, "p": provider, "e": ext, "m": '{"plan_code": "%s"}' % plan})
            await s.execute(text(sql.replace("WHERE kind IS NULL", "WHERE kind IS NULL AND telegram_user_id = :t")),
                            {"t": tg})
            rows = dict((await s.execute(text(
                "SELECT external_id, kind FROM payments WHERE telegram_user_id = :t"), {"t": tg})).all())
            await s.rollback()
        assert rows == {f"a{tg}": "obhod_package", f"b{tg}": "subscription", f"c{tg}": "promo",
                        f"d{tg}": "subscription"}
    finally:
        await _cleanup(Session, tg)
        await engine.dispose()
