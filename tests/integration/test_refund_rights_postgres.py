"""Refund rights survive SQL JSON persistence and a provisioning restart."""
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.domain.models import Entitlement
from app.domain.models import EntitlementSource as Src
from tests.integration.test_money_real_postgres import _cleanup, _NoObhod

PG_URL = os.getenv("HOTFIX_PG_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not PG_URL, reason="HOTFIX_PG_URL is not set")]


@pytest.mark.parametrize("first,second,source", [
    ("lite", "pro", Src.PAYMENT), ("pro", "lite", Src.PAYMENT),
    ("lite", "pro", Src.AUTORENEW),
])
@pytest.mark.parametrize("later", [False, True])
async def test_refund_after_restart_preserves_later_rights(first, second, source, later):
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import RemnaUser
    from app.services.accounts import SqlAccountsRepo
    from app.services.provisioning import PanelProvisioningService
    from tests.fakes.notifier import RecordingNotifier
    from tests.fakes.redis import FakeRedis
    from tests.fakes.remnawave import FakeRemnaGateway

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tg = 740000000 + uuid.uuid4().int % 1000000
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    gw = FakeRemnaGateway()
    gw.fake.now = now
    gw.fake._next_id = 100000 + uuid.uuid4().int % 100000

    def service():
        return PanelProvisioningService(
            gw, SqlAccountsRepo(Session), notifier=RecordingNotifier(), obhod=_NoObhod(),
            clock=lambda: now, settings=SimpleNamespace(), late_patch_delay_s=0,
        )

    try:
        with patch("app.services.cache.get_redis_client", return_value=FakeRedis()):
            await service().grant(tg, Entitlement(plan_code=first, source=Src.PAYMENT, payment_id=101),
                                  trace_id="first", months=1)
            uid = gw.fake.created[0]["id"]
            before = dict(gw.fake.users[uid])
            await service().grant(tg, Entitlement(plan_code=second, source=source, payment_id=102),
                                  trace_id="second", months=1)
            def parse(value):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            credited = parse(gw.fake.users[uid]["expireAt"]) - parse(before["expireAt"])
            if later:
                await service().grant(tg, Entitlement(plan_code="standard", source=Src.GIFT),
                                      trace_id="later-gift", months=1)
            expected_limit = max(before["hwidDeviceLimit"], 5) if later else before["hwidDeviceLimit"]
            expected_expiry = parse(gw.fake.users[uid]["expireAt"]) - credited
            result = await service().rollback(tg, months=1, payment_id=102, reason="refund", trace_id="refund")
            assert result.changed
            user = gw.fake.users[uid]
            assert datetime.fromisoformat(user["expireAt"].replace("Z", "+00:00")) == expected_expiry
            assert gw.fake.squad_names(uid) == ["standard" if later else first]
            assert user["hwidDeviceLimit"] == expected_limit
            row = await SqlAccountsRepo(Session).get_subscription(tg)
            assert row.plan_code == ("standard" if later else first)
            patches = len(gw.fake.patches)
            assert (await service().rollback(tg, months=1, payment_id=102, reason="refund",
                                             trace_id="retry-after-restart")).changed
            assert len(gw.fake.patches) == patches
    finally:
        await _cleanup(Session, tg)
        async with Session() as session:
            for uid in gw.fake.users:
                await session.execute(delete(RemnaUser).where(RemnaUser.remna_id == str(uid)))
            await session.commit()
        await engine.dispose()
