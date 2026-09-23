"""Stream C: SqlEventsRepo on real Postgres (sub_kind filters, grace columns, last plan).

Run: HOTFIX_PG_URL=postgresql+asyncpg://u:p@127.0.0.1:55432/crs pytest -m integration \
     tests/integration/test_events_repo_real_postgres.py
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = [pytest.mark.integration]

PG_URL = os.getenv("HOTFIX_PG_URL")


@pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL не задан")
async def test_events_repo_on_postgres():
    from sqlalchemy import delete
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Payment, RemnaUser, Subscription, TelegramUser
    from app.services.events_repo import (
        EXPIRE_ALREADY,
        EXPIRE_MARKED,
        EXPIRE_NO_ROW,
        EXPIRE_PAID_LATER,
        GRACE_ACTIVE,
        SqlEventsRepo,
    )

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlEventsRepo(Session)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    tg = 710000000 + (uuid.uuid4().int % 1000000)
    tg2 = tg + 1
    pid, obhod_pid = tg * 10 + 5, tg * 10 + 6
    naive = lambda dt: dt.replace(tzinfo=None)  # noqa: E731
    try:
        async with Session() as s:
            for t in (tg, tg2):
                await s.execute(pg_insert(TelegramUser).values(telegram_id=t).on_conflict_do_nothing())
            for r in (pid, obhod_pid):
                await s.execute(pg_insert(RemnaUser).values(remna_id=str(r)).on_conflict_do_nothing())
            s.add(Subscription(telegram_user_id=tg, plan_code="pro", sub_kind="main", active=True,
                               valid_until=naive(now - timedelta(minutes=1)), remna_user_id=str(pid)))
            s.add(Subscription(telegram_user_id=tg, plan_code="obhod", sub_kind="obhod", active=True,
                               valid_until=naive(now - timedelta(minutes=1)), remna_user_id=str(obhod_pid)))
            s.add(Subscription(telegram_user_id=tg2, plan_code="standard", sub_kind="main", active=True,
                               valid_until=naive(now + timedelta(days=20)), autorenew=True))
            s.add(Payment(telegram_user_id=tg, amount=1199, provider="yookassa", external_id=f"ev-{tg}-1",
                          status="succeeded", paid_at=naive(now - timedelta(days=90)),
                          payment_metadata={"plan_code": "pro", "period_months": 3}))
            s.add(Payment(telegram_user_id=tg, amount=599, provider="yookassa", external_id=f"ev-{tg}-2",
                          status="succeeded", paid_at=naive(now - timedelta(days=10)), kind="obhod_package",
                          plan_code="obhod_250", period_months=1))
            s.add(Payment(telegram_user_id=tg, amount=0, provider="promo", external_id=f"ev-{tg}-3",
                          status="succeeded", paid_at=naive(now - timedelta(days=1)),
                          payment_metadata={"plan_code": "lite", "period_months": 1}))
            s.add(Payment(telegram_user_id=tg2, amount=249, provider="yookassa", external_id=f"ev-{tg2}-1",
                          status="refunded", paid_at=naive(now - timedelta(days=5)), plan_code="standard",
                          period_months=1))
            await s.commit()

        info = await repo.reminder_info(tg)
        assert (info.last_plan_code, info.last_months) == ("pro", 3)
        assert info.has_paid and not info.refunded and not info.autorenew and info.grace_state is None
        info2 = await repo.reminder_info(tg2)
        assert info2.refunded and info2.autorenew and not info2.has_paid

        assert await repo.obhod_panel_id(tg) == str(obhod_pid)
        assert await repo.obhod_owner(obhod_pid) == tg and await repo.obhod_owner(pid) is None

        assert await repo.mark_main_expired(tg2, now) == EXPIRE_PAID_LATER
        assert await repo.mark_main_expired(tg, now) == EXPIRE_MARKED
        assert await repo.mark_main_expired(tg, now) == EXPIRE_ALREADY
        assert await repo.mark_main_expired(tg + 99, now) == EXPIRE_NO_ROW
        assert await repo.deactivate_obhod_row(tg) is True
        assert await repo.obhod_panel_id(tg) is None

        until = now + timedelta(days=3)
        assert await repo.set_grace(tg, until=until, state=GRACE_ACTIVE)
        row = await repo.get_grace(tg)
        assert row.grace_until == until and row.grace_state == GRACE_ACTIVE and row.remna_user_id == str(pid)
        assert tg not in [r.telegram_id for r in await repo.due_graces(now)]
        assert tg in [r.telegram_id for r in await repo.due_graces(until + timedelta(seconds=1))]
        assert (await repo.reminder_info(tg)).grace_state == GRACE_ACTIVE
    finally:
        async with Session() as s:
            await s.execute(delete(Payment).where(Payment.telegram_user_id.in_((tg, tg2))))
            await s.execute(delete(Subscription).where(Subscription.telegram_user_id.in_((tg, tg2))))
            await s.execute(delete(TelegramUser).where(TelegramUser.telegram_id.in_((tg, tg2))))
            await s.execute(delete(RemnaUser).where(RemnaUser.remna_id.in_((str(pid), str(obhod_pid)))))
            await s.commit()
        await engine.dispose()
