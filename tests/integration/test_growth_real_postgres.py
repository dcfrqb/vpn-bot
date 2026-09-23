"""Stream E on real Postgres: record-first promo races with Redis down, code
reservations (FOR UPDATE), gifts, segments, the broadcast worker with
credit_days replayed after a "restart", sun718 revert.

Run: HOTFIX_PG_URL=postgresql+asyncpg://u:p@127.0.0.1:55441/crs pytest -m integration \
     tests/integration/test_growth_real_postgres.py
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.getenv("HOTFIX_PG_URL"), reason="HOTFIX_PG_URL не задан")]

PG_URL = os.getenv("HOTFIX_PG_URL")


@pytest.fixture
async def db(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.session.SessionLocal", Session)
    monkeypatch.setattr("app.services.cache.get_redis_client", lambda: None)  # Redis down: DB is the only guard
    yield Session
    await engine.dispose()


def _uid() -> int:
    return 800000000 + uuid.uuid4().int % 99000000


def _engine(Session, prov, status, **settings):
    from app.services.promo import PromoEngine
    from app.services.promo_repo import SqlPromoRepo
    from tests.fakes.notifier import RecordingNotifier
    from tests.growth.fakes import Settings

    return PromoEngine(provisioning=prov, status=status, notifier=RecordingNotifier(), repo=SqlPromoRepo(Session),
                       settings=Settings(**settings))


async def test_parallel_trial_on_postgres_one_grant(db):
    from sqlalchemy import func, select

    from app.db.models import Payment, PromoRedemption, Trial
    from tests.growth.fakes import FakeProvisioning, FakeStatus

    status = FakeStatus()
    prov = FakeProvisioning(status, delay=0.02)
    engine = _engine(db, prov, status)
    tg = _uid()
    results = await asyncio.gather(*(engine.start_trial(tg) for _ in range(5)))
    assert sum(r.applied for r in results) == 1 and prov.effective == 1
    async with db() as s:
        assert (await s.execute(select(func.count(Trial.id)).where(Trial.telegram_user_id == tg))).scalar_one() == 1
        assert (await s.execute(select(func.count(Payment.id)).where(
            Payment.external_id == f"promo_trial_{tg}"))).scalar_one() == 1
        st = (await s.execute(select(PromoRedemption.status).where(PromoRedemption.telegram_user_id == tg))).scalars().all()
        assert st == ["applied"]
    assert not await engine.trial_available(tg)


async def test_failed_grant_leaves_no_record_on_postgres(db):
    from sqlalchemy import select

    from app.db.models import Payment, Trial
    from tests.growth.fakes import FakeProvisioning, FakeStatus

    status = FakeStatus()
    prov = FakeProvisioning(status)
    prov.fail = True
    engine = _engine(db, prov, status)
    tg = _uid()
    assert not (await engine.start_trial(tg)).applied
    async with db() as s:
        assert (await s.execute(select(Payment.id).where(Payment.external_id == f"promo_trial_{tg}"))).first() is None
        assert (await s.execute(select(Trial.id).where(Trial.telegram_user_id == tg))).first() is None
    prov.fail = False
    assert (await engine.start_trial(tg)).applied


async def test_code_reservations_are_atomic_on_postgres(db):
    from app.services.promo import PromoCodeSpec
    from tests.growth.fakes import FakeProvisioning, FakeStatus

    status = FakeStatus()
    prov = FakeProvisioning(status, delay=0.01)
    engine = _engine(db, prov, status)
    code = f"it{uuid.uuid4().hex[:10]}"
    row = await engine.create_code(PromoCodeSpec(code=code, days=3, plan_code="lite", max_uses=2, traffic_gb=10))
    assert row.traffic_gb == 10
    users = [_uid() for _ in range(6)]
    res = await asyncio.gather(*(engine.redeem(u, code) for u in users))
    assert sum(r.applied for r in res) == 2 and prov.effective == 2
    assert (await engine.repo.get_code(code)).uses == 2

    code2 = f"it{uuid.uuid4().hex[:10]}"
    await engine.create_code(PromoCodeSpec(code=code2, days=3, plan_code="lite"))
    tg = _uid()
    res = await asyncio.gather(*(engine.redeem(tg, code2) for _ in range(5)))
    assert sum(r.applied for r in res) == 1


async def test_gift_on_postgres(db):
    from tests.growth.fakes import FakeProvisioning, FakeStatus

    status = FakeStatus()
    prov = FakeProvisioning(status, delay=0.01)
    engine = _engine(db, prov, status)
    buyer, pid = _uid(), int(uuid.uuid4().int % 10 ** 9)
    await engine.repo.ensure_user(buyer)
    code = await engine.create_gift(buyer, "standard", 1, payment_id=pid)
    assert await engine.create_gift(buyer, "standard", 1, payment_id=pid) == code
    res = await asyncio.gather(*(engine.redeem(_uid(), code) for _ in range(4)))
    assert sum(r.applied for r in res) == 1


async def _mk_user(s, tg, **kw):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.models import TelegramUser

    await s.execute(pg_insert(TelegramUser).values(telegram_id=tg, **kw).on_conflict_do_nothing())


async def test_segments_on_postgres(db):
    from sqlalchemy import select

    from app.db.models import Payment, Subscription, TelegramUser, Trial
    from app.services.broadcast import Segment, segment_filter

    now = datetime.utcnow()
    u = {k: _uid() for k in ("active", "soon", "expired_recent", "expired_old", "never", "trial_nc", "trial_paid",
                             "optout", "obhod_only")}
    async with db() as s:
        for k, tg in u.items():
            await _mk_user(s, tg, broadcast_opt_out=(k == "optout"))
        await s.flush()

        def sub(tg, until, kind="main", active=True):
            s.add(Subscription(telegram_user_id=tg, plan_code="standard", sub_kind=kind, active=active, valid_until=until))

        sub(u["active"], now + timedelta(days=20))
        sub(u["soon"], now + timedelta(days=2))
        sub(u["expired_recent"], now - timedelta(days=3), active=False)
        sub(u["expired_old"], now - timedelta(days=60), active=False)
        sub(u["optout"], now + timedelta(days=20))
        sub(u["obhod_only"], now + timedelta(days=20), kind="obhod")
        for k in ("active", "soon", "trial_paid"):
            s.add(Payment(telegram_user_id=u[k], provider="yookassa", external_id=f"it-{uuid.uuid4().hex}",
                          amount=249, status="succeeded", paid_at=now))
        s.add(Payment(telegram_user_id=u["never"], provider="promo", external_id=f"promo_solokhin_{u['never']}",
                      amount=0, status="succeeded", paid_at=now))
        for k in ("trial_nc", "trial_paid"):
            s.add(Trial(telegram_user_id=u[k], plan_code="standard", days=5, started_at=now - timedelta(days=2)))
        await s.commit()

        async def members(seg):
            rows = await s.execute(select(TelegramUser.telegram_id).where(
                segment_filter(seg), TelegramUser.telegram_id.in_(list(u.values()))))
            inv = {v: k for k, v in u.items()}
            return {inv[r[0]] for r in rows.all()}

        assert await members(Segment("active")) == {"active", "soon"}
        assert await members(Segment("active", days=7)) == {"soon"}
        assert await members(Segment("active", sub_kind="obhod")) == {"obhod_only"}
        assert await members(Segment("expired")) == {"expired_recent", "expired_old"}
        assert await members(Segment("expired", days=7)) == {"expired_recent"}
        assert await members(Segment("never")) == {"expired_recent", "expired_old", "never", "trial_nc", "obhod_only"}
        assert await members(Segment("trial_nc")) == {"trial_nc"}
        assert await members(Segment("trial_nc", days=1)) == set()
        assert await members(Segment("ids", ids=(u["optout"], u["never"]))) == {"optout", "never"}
        assert "optout" not in await members(Segment("all"))


class _Sender:
    def __init__(self):
        self.sent = []

    async def send(self, user_id, *, text_html, photo_file_id, buttons, disable_notification):
        from app.services.broadcast import SendResult

        self.sent.append(user_id)
        return SendResult("sent")


async def test_broadcast_worker_credit_days_replay_no_double_grant(db, monkeypatch):
    from sqlalchemy import select, update

    from app.db.models import Broadcast, BroadcastRecipient
    from app.services import broadcast as svc
    from app.services.grants import SqlRedemptionLedger
    from tests.growth.fakes import FakeProvisioning, FakeStatus

    monkeypatch.setattr(svc, "SEND_INTERVAL", 0)
    status = FakeStatus()
    prov = FakeProvisioning(status)
    users = [_uid() for _ in range(3)]
    async with db() as s:
        for tg in users:
            await _mk_user(s, tg)
        await s.commit()
    for tg in users[:2]:
        status.set(tg, active=True, plan_code="standard", expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    bid = await svc.create_draft(text_html="hi", photo_file_id=None, buttons=None,
                                 segment=svc.Segment("ids", ids=tuple(users)), disable_notification=True,
                                 credit_days=5, created_by=users[0])
    credit = svc.BroadcastCredits(provisioning=prov, status=status, ledger=SqlRedemptionLedger(db))
    sender = _Sender()

    async def run_once():
        assert await svc.start_broadcast(sender, bid, credit=credit)
        await asyncio.gather(*(h.task for h in list(svc._active_workers.values())))

    await run_once()
    assert sorted(sender.sent) == sorted(users)
    assert prov.effective == 2  # the third user has no subscription -> skipped
    # "restart": the broadcast is resumed and one delivered credit was left pending
    async with db() as s:
        await s.execute(update(Broadcast).where(Broadcast.id == bid).values(finished_at=None))
        await s.commit()
    ledger = SqlRedemptionLedger(db)
    await ledger.open(svc.credit_code(bid), users[0], {"days": 5})
    await run_once()
    assert sorted(sender.sent) == sorted(users)  # nobody got the message twice
    assert prov.effective == 2  # nobody got the days twice
    assert await ledger.count(svc.credit_code(bid), "applied") == 2
    async with db() as s:
        st = (await s.execute(select(BroadcastRecipient.status).where(BroadcastRecipient.broadcast_id == bid))).scalars()
        assert set(st) == {"sent"}
        bc = await s.get(Broadcast, bid)
        assert bc.finished_at is not None and bc.segment == "ids" and bc.credit_days == 5


async def test_sun718_revert_on_postgres(db):
    from app.db.models import Payment
    from app.services.referral import Sun718Reverter
    from tests.fakes.notifier import RecordingNotifier
    from tests.fakes.remnawave import FakeRemnaGateway

    tg = _uid()
    panel_id = 60000 + tg % 1000
    gw = FakeRemnaGateway()
    gw.fake.add_user(panel_id, f"tg_{tg}", telegram_id=tg, squads=["pro", "pro-m", "us-2"], limit=10)
    async with db() as s:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.models import RemnaUser

        await s.execute(pg_insert(RemnaUser).values(remna_id=str(panel_id)).on_conflict_do_nothing())
        await _mk_user(s, tg, remna_user_id=str(panel_id))
        s.add(Payment(telegram_user_id=tg, provider="promo", external_id=f"promo_sun718_{tg}", amount=0,
                      status="succeeded", paid_at=datetime.utcnow() - timedelta(days=6),
                      payment_metadata={"promo_code": "sun718", "revert_at": (datetime.utcnow() - timedelta(hours=1)).isoformat(),
                                        "pre_promo_plan": "standard", "revert_completed": False}))
        await s.commit()
    notifier = RecordingNotifier()
    rev = Sun718Reverter(remna=gw, notifier=notifier, session_factory=db)
    assert await rev.run() >= 1
    assert set(gw.fake.squad_names(panel_id)) == {"standard", "pro-m", "us-2"}
    assert gw.fake.users[panel_id]["hwidDeviceLimit"] == 10
    assert await rev.run() == 0
    async with db() as s:
        from sqlalchemy import select

        p = (await s.execute(select(Payment).where(Payment.external_id == f"promo_sun718_{tg}"))).scalar_one()
        assert p.payment_metadata["revert_completed"] is True and p.payment_metadata["reverted_to_plan"] == "standard"
    assert any("REVERT" in m.text for m in notifier.sent)


async def test_referral_stats_on_postgres(db):
    from app.services.referral import ReferralService
    from tests.fakes.notifier import RecordingNotifier
    from tests.growth.fakes import Settings

    svc = ReferralService(notifier=RecordingNotifier(), settings=Settings(), session_factory=db)
    st = await svc.stats("sun718")
    assert st is None or st.available >= 0
