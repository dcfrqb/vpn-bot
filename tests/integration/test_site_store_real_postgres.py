"""Real SQL persistence and cross-connection locks for website purchases."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import session as db
from app.db.models import SiteCustomer, SiteOrder
from app.services.site_commerce import SiteCommerce, SiteCommerceError
from app.services.site_store import SiteBusy, SqlSiteStore
from tests.test_site_commerce import service

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv("HOTFIX_PG_URL"), reason="HOTFIX_PG_URL unset"),
]


async def test_sql_lock_and_restart_after_panel_response_loss(monkeypatch):
    engine = create_async_engine(os.environ["HOTFIX_PG_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessions)
    account, key = str(uuid4()), str(uuid4())
    store = SqlSiteStore()
    s = service()
    s.store = store
    s.clock = lambda: datetime.now(timezone.utc)
    try:
        async with store.lock(account):
            await store.save_customer(
                {
                    "account_id": account,
                    "backend": "web",
                    "telegram_id": None,
                    "owner_tag": "test",
                }
            )
            # A committed DB write must not release the account lock.
            with pytest.raises(SiteBusy):
                async with SqlSiteStore().lock(account):
                    pytest.fail("another connection acquired held lock")
        async with store.lock(account):
            pass  # connection returned unlocked
        order = await s.start(account, None, "pro", 1, key, 449)
        raw = await store.order(key)
        assert raw["created_at"].tzinfo is not None
        s.provider.data[raw["provider_id"]].update(status="succeeded", paid=True)
        s.panel.fail_after_create = True
        assert (await s.refresh(account, key))["state"] == "provisioning"
        target = (await store.order(key))["target_expire"]
        restarted = SiteCommerce(
            s.container,
            store=SqlSiteStore(),
            provider=s.provider,
            panel=s.panel,
            clock=s.clock,
        )
        assert (await restarted.refresh(account, key))["state"] == "fulfilled"
        assert len(s.panel.users) == 2
        assert all(u.expire_at.isoformat() == target for u in s.panel.users.values())
        assert (await store.external(raw["provider_id"]))["id"] == order["id"]
        assert any(o["id"] == key for o in await store.recovery())
        with pytest.raises(SiteCommerceError) as exc:
            await restarted.refresh(str(uuid4()), key)
        assert exc.value.status == 404
    finally:
        async with sessions() as session:
            await session.execute(
                delete(SiteOrder).where(SiteOrder.account_id == account)
            )
            await session.execute(
                delete(SiteCustomer).where(SiteCustomer.account_id == account)
            )
            await session.commit()
        await engine.dispose()
