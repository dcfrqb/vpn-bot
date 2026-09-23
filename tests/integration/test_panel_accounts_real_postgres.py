"""Stream B on real Postgres: SqlAccountsRepo + ProvisioningService + panel_sync.

Run: HOTFIX_PG_URL=postgresql+asyncpg://u:p@127.0.0.1:55432/crs pytest -m integration \
     tests/integration/test_panel_accounts_real_postgres.py
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]
PG_URL = os.getenv("HOTFIX_PG_URL")


class _NoObhod:
    async def on_main_granted(self, *a):
        pass

    async def on_main_revoked(self, *a):
        pass


@pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL is not set")
async def test_grant_then_repeat_then_pull_forward_on_real_postgres():
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import RemnaUser, Subscription, TelegramUser
    from app.domain.models import Entitlement, EntitlementSource, SubKind
    from app.services.accounts import SqlAccountsRepo
    from app.services.panel_sync import PanelSync
    from app.services.provisioning import PanelProvisioningService
    from tests.fakes.redis import FakeRedis
    from tests.fakes.remnawave import FakeRemnaGateway

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tg = 720000000 + (uuid.uuid4().int % 1000000)
    repo = SqlAccountsRepo(Session)
    gw = FakeRemnaGateway()
    try:
        with patch("app.services.cache.get_redis_client", return_value=FakeRedis()):
            svc = PanelProvisioningService(gw, repo, obhod=_NoObhod(), settings=SimpleNamespace(),
                                           late_patch_delay_s=0)
            ent = Entitlement(plan_code="standard", source=EntitlementSource.TRIAL, days=5)
            st = await svc.grant(tg, ent, trace_id=f"it-{tg}")
            assert st.active and len(gw.fake.created) == 1
            panel_id = gw.fake.created[0]["id"]
            # retry of the same trace: no second write, no second row
            patches = len(gw.fake.patches)
            await svc.grant(tg, ent, trace_id=f"it-{tg}")
            assert len(gw.fake.patches) == patches

        async with Session() as s:
            u = (await s.execute(select(TelegramUser).where(TelegramUser.telegram_id == tg))).scalar_one()
            rows = (await s.execute(select(Subscription).where(
                Subscription.telegram_user_id == tg, Subscription.sub_kind == "main"))).scalars().all()
        assert u.remna_user_id == str(panel_id)
        assert len(rows) == 1 and rows[0].active and rows[0].provisioning_state == "synced"
        assert rows[0].config_data["grants"][f"trace:it-{tg}"]["state"] == "applied"
        assert rows[0].valid_until.tzinfo is None  # naive UTC in the DB

        # manual extension in the panel -> pulled into valid_until
        far = datetime.now(timezone.utc) + timedelta(days=90)
        gw.fake.users[panel_id]["expireAt"] = far.strftime("%Y-%m-%dT%H:%M:%SZ")
        report = await PanelSync(gw, repo, settings=SimpleNamespace()).run()
        assert report.pulled_forward >= 1
        row = await repo.get_subscription(tg, SubKind.MAIN)
        assert abs((row.valid_until - far).total_seconds()) < 2
    finally:
        async with Session() as s:
            await s.execute(delete(Subscription).where(Subscription.telegram_user_id == tg))
            await s.execute(delete(TelegramUser).where(TelegramUser.telegram_id == tg))
            await s.execute(delete(RemnaUser).where(RemnaUser.remna_id.in_([str(c["id"]) for c in gw.fake.created])))
            await s.commit()
        await engine.dispose()
