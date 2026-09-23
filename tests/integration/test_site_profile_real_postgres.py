"""Профиль для сайта на реальном Postgres: load_db_snapshot + сборка профиля.

Запуск: HOTFIX_PG_URL=postgresql+asyncpg://u:p@127.0.0.1:55432/crs pytest -m integration \
        tests/integration/test_site_profile_real_postgres.py
(схема накатана alembic upgrade head)
"""
import os
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

PG_URL = os.getenv("HOTFIX_PG_URL")


@pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL не задан")
@pytest.mark.asyncio
async def test_profile_from_real_rows_hides_raw_data_and_urls():
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Payment, RemnaUser, Subscription, TelegramUser
    from app.services import site_profile as sp
    from tests.fakes.redis import FakeRedis

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    uid = 710000000 + (uuid.uuid4().int % 1000000)
    main_id = str(6000 + uuid.uuid4().int % 100000)
    obhod_id = str(int(main_id) + 1)
    try:
        async with Session() as s:
            s.add_all([
                RemnaUser(remna_id=main_id, raw_data={"subscriptionUrl": "https://sub.example/RAWSECRET"}),
                RemnaUser(remna_id=obhod_id, raw_data={"ssPassword": "RAWSECRET2"}),
            ])
            await s.flush()
            s.add(TelegramUser(telegram_id=uid, username="u", first_name="F", remna_user_id=main_id,
                               created_at=datetime(2025, 12, 1, 10, 0, 0)))
            await s.flush()
            s.add_all([
                Subscription(telegram_user_id=uid, remna_user_id=main_id, plan_code="pro", sub_kind="main",
                             active=True, config_data={"subscription_url": "https://sub.example/CFGSECRET"}),
                Subscription(telegram_user_id=uid, remna_user_id=obhod_id, plan_code="obhod", sub_kind="obhod",
                             active=True, config_data={"subscription_url": "https://sub.example/CFGSECRET2",
                                                       "package": "obhod_500",
                                                       "package_until": "2026-10-20T00:00:00",
                                                       "package_limit_bytes": 536870912000}),
                Payment(telegram_user_id=uid, provider="yookassa", external_id=f"sp-{uid}-1", amount=449,
                        status="succeeded", paid_at=datetime(2026, 9, 1, 10, 1),
                        created_at=datetime(2026, 9, 1, 10, 0),
                        payment_metadata={"plan_code": "pro", "period_months": "1"}),
                Payment(telegram_user_id=uid, provider="promo", external_id=f"sp-{uid}-2", amount=0,
                        status="succeeded", created_at=datetime(2026, 8, 1, 10, 0),
                        payment_metadata={"promo_code": "trial", "tariff": "trial_standard_5d"}),
            ])
            await s.commit()

        async def no_last_plan(_):
            return None

        with patch("app.db.session.SessionLocal", Session), \
             patch("app.services.users.get_user_last_plan", no_last_plan), \
             patch("app.services.cache.get_redis_client", return_value=FakeRedis()):
            profile = await sp.get_profile(uid)

        import json
        text = json.dumps(profile, ensure_ascii=False)
        assert "SECRET" not in text
        assert [a["kind"] for a in profile["accounts"]] == ["main", "obhod"]
        assert profile["accounts"][0]["remna_id"] == int(main_id)
        assert profile["accounts"][1]["remna_id"] == int(obhod_id)
        assert profile["accounts"][1]["package"] == {
            "code": "obhod_500", "until": "2026-10-20T00:00:00Z", "limit_bytes": 536870912000}
        assert [p["kind"] for p in profile["payments"]] == ["subscription", "promo"]
        assert profile["payments"][1]["description"] == "Промокод trial: Standard, 5 дн"
        assert profile["stats"]["payments_count"] == 1 and profile["stats"]["paid_total_rub"] == 449.0
        assert profile["user"]["customer_since"] == "2025-12-01T10:00:00Z"
    finally:
        async with Session() as s:
            await s.execute(delete(Payment).where(Payment.telegram_user_id == uid))
            await s.execute(delete(Subscription).where(Subscription.telegram_user_id == uid))
            await s.execute(delete(TelegramUser).where(TelegramUser.telegram_id == uid))
            await s.execute(delete(RemnaUser).where(RemnaUser.remna_id.in_([main_id, obhod_id])))
            await s.commit()
        await engine.dispose()
