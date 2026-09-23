"""Хотфикс 2.1 на реальном Postgres (опционально).

Запуск: HOTFIX_PG_URL=postgresql+asyncpg://u:p@127.0.0.1:55432/crs pytest -m integration \
        tests/integration/test_hotfix_real_postgres.py
БД должна быть на alembic head. Без HOTFIX_PG_URL тест пропускается.
"""
import os
import uuid

import pytest

pytestmark = [pytest.mark.integration]

PG_URL = os.getenv("HOTFIX_PG_URL")




@pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL не задан")
@pytest.mark.asyncio
async def test_blocklist_reads_migrated_tables():
    from unittest.mock import patch

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services import blocklist

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    uid = 800000000 + (uuid.uuid4().int % 1000000)
    try:
        async with Session() as s:
            await s.execute(text("INSERT INTO blocked_users (telegram_id, reason) VALUES (:t, 'competitor')"), {"t": uid})
            await s.commit()
        with patch.object(blocklist, "SessionLocal", Session):
            assert await blocklist.get_user_block_reason(uid) == "competitor"
    finally:
        async with Session() as s:
            await s.execute(text("DELETE FROM blocked_users WHERE telegram_id = :t"), {"t": uid})
            await s.commit()
        await engine.dispose()
