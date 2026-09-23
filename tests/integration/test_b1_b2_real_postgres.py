"""Фикс-раунд 3 (B1, B2) на реальном Postgres: identity map настоящей AsyncSession.

B1: первая оплата юзера с telegram_users.remna_user_id = NULL (Phase B находит
юзера по telegramId и пишет id в своей сессии) не должна падать в failed из-за
устаревшего чтения внешней сессии.
B2: оплаченный срок добавляется к текущему expireAt в панели (триал/промо).

Запуск: HOTFIX_PG_URL=postgresql+asyncpg://u:p@127.0.0.1:55432/crs pytest -m integration \
        tests/integration/test_b1_b2_real_postgres.py
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from dateutil.relativedelta import relativedelta

pytestmark = [pytest.mark.integration]

PG_URL = os.getenv("HOTFIX_PG_URL")


class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return 1 if key in self.store else 0


# test_first_payment_with_null_remna_id_is_synced_and_stacks_on_trial: removed in 3.0 with the 2.x provisioning (same scenario through Fulfillment + stream B provisioning: tests/integration/test_money_real_postgres.py)


@pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL не задан")
@pytest.mark.asyncio
async def test_start_and_grant_persist_remna_id():
    """/start (get_or_create_telegram_user) только ищет аккаунт панели (3.0), выдача создает;
    оба пути записывают id панели в БД."""
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import RemnaUser, TelegramUser
    from app.services import remna_service, users as users_service
    from tests.fakes.remnawave import FakeRemna

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    uid = 710000000 + (uuid.uuid4().int % 1000000)
    fake = FakeRemna()

    class _Client:
        async def get_or_create_user(self, telegram_id, **kw):
            u = await fake.get_user_by_telegram_id(telegram_id)
            if u:
                return u
            await fake.create_user(f"tg_{telegram_id}", telegram_id=telegram_id)
            return await fake.get_user_by_telegram_id(telegram_id)

        async def get_user_by_telegram_id(self, telegram_id, strict=False):
            return await fake.get_user_by_telegram_id(telegram_id, strict=strict)

        async def close(self):
            return None

    try:
        # 3.0 (stream B): /start only looks up, it never creates a panel account.
        with patch("app.db.session.SessionLocal", Session), \
             patch.object(remna_service, "RemnaClient", return_value=_Client()):
            await users_service.get_or_create_telegram_user(uid, username=None, first_name="Test")
        async with Session() as s:
            tg = (await s.execute(select(TelegramUser).where(TelegramUser.telegram_id == uid))).scalar_one()
        assert tg.remna_user_id is None and not fake.created

        # A grant path creates it and stores the link.
        with patch("app.db.session.SessionLocal", Session), \
             patch.object(remna_service, "RemnaClient", return_value=_Client()):
            await remna_service.ensure_user_in_remnawave(uid)
        # A later /start finds the existing account and keeps the link.
        with patch("app.db.session.SessionLocal", Session), \
             patch.object(remna_service, "RemnaClient", return_value=_Client()):
            await users_service.get_or_create_telegram_user(uid, username=None, first_name="Test")
        async with Session() as s:
            tg = (await s.execute(select(TelegramUser).where(TelegramUser.telegram_id == uid))).scalar_one()
            rid = tg.remna_user_id
        assert rid is not None and int(rid) in fake.users and len(fake.created) == 1

        # Существующую привязку не перезаписываем
        with patch("app.db.session.SessionLocal", Session):
            await remna_service.persist_remna_link(uid, "999999")
        async with Session() as s:
            tg = (await s.execute(select(TelegramUser).where(TelegramUser.telegram_id == uid))).scalar_one()
        assert tg.remna_user_id == rid
    finally:
        async with Session() as s:
            await s.execute(delete(TelegramUser).where(TelegramUser.telegram_id == uid))
            await s.execute(delete(RemnaUser).where(RemnaUser.remna_id.in_([str(k) for k in fake.users] + ["999999"])))
            await s.commit()
        await engine.dispose()
