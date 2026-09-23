"""3.0: every port has its real implementation wired by the container."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.container import Container, build_container, get_container, set_container
from app.services import ports
from tests.fakes.bot import make_bot
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.payments import FakePaymentGateway
from tests.fakes.redis import FakeRedis
from tests.fakes.remnawave import FakeRemna, FakeRemnaGateway
from tests.fakes.stars import FakeStarsGateway

PORT_OF_FIELD = {
    "remna": ports.RemnaGateway, "payments": ports.PaymentGateway, "stars": ports.StarsGateway,
    "provisioning": ports.ProvisioningService, "status": ports.StatusService,
    "devices": ports.DevicesService, "checkout": ports.CheckoutService, "promo": ports.PromoService,
    "notifier": ports.Notifier, "maintenance": ports.MaintenanceGuard,
}


def test_default_container_satisfies_every_port():
    bot, _ = make_bot()
    c = build_container(bot)
    for field, proto in PORT_OF_FIELD.items():
        assert isinstance(getattr(c, field), proto), field
    assert set(ports.__all__) == {p.__name__ for p in PORT_OF_FIELD.values()}


def test_fakes_satisfy_ports():
    assert isinstance(FakeRemnaGateway(), ports.RemnaGateway)
    assert isinstance(FakePaymentGateway(), ports.PaymentGateway)
    assert isinstance(FakeStarsGateway(), ports.StarsGateway)
    assert isinstance(RecordingNotifier(), ports.Notifier)


def test_overrides_and_unknown_override():
    bot, _ = make_bot()
    pay = FakePaymentGateway()
    c = build_container(bot, payments=pay)
    assert c.payments is pay and c.checkout._impl().d.payments is pay  # checkout uses the same gateway
    with pytest.raises(TypeError):
        build_container(bot, nope=1)
    with pytest.raises(TypeError):
        build_container(bot, bot=1)


def test_get_container_requires_startup():
    set_container(None)
    with pytest.raises(RuntimeError):
        get_container()
    bot, _ = make_bot()
    c = build_container(bot)
    set_container(c)
    assert get_container() is c
    set_container(None)


def test_container_wires_the_real_services_over_one_gateway():
    from app.infra.remnawave.gateway import HttpRemnaGateway
    from app.infra.telegram_stars import TelegramStarsGateway
    from app.infra.yookassa.gateway import YooKassaGateway
    from app.services.devices import PanelDevicesService
    from app.services.promo import PromoEngine
    from app.services.provisioning import PanelProvisioningService
    from app.services.status import PanelStatusService

    bot, _ = make_bot()
    c = build_container(bot)
    assert isinstance(c.remna, HttpRemnaGateway) and isinstance(c.payments, YooKassaGateway)
    assert isinstance(c.stars, TelegramStarsGateway)
    assert isinstance(c.provisioning, PanelProvisioningService) and c.provisioning.remna is c.remna
    assert isinstance(c.status, PanelStatusService) and c.status.remna is c.remna
    assert isinstance(c.devices, PanelDevicesService) and c.devices.remna is c.remna
    assert isinstance(c.promo, PromoEngine) and c.promo.provisioning is c.provisioning


def test_dependents_are_built_over_an_overridden_gateway():
    bot, _ = make_bot()
    fake = FakeRemnaGateway()
    c = build_container(bot, remna=fake)
    assert c.provisioning.remna is fake and c.status.remna is fake and c.devices.remna is fake


# --- LegacyRemnaGateway over the in-memory panel -------------------------------------------------

@pytest.fixture
def gw():
    fake = FakeRemna()
    fake.add_user(501, "tg_1", telegram_id=1, squads=["lite", "pro-m", "arcadia"], limit=15)
    return FakeRemnaGateway(fake)


async def test_get_find_and_url(gw):
    u = await gw.get_user(501)
    assert u.username == "tg_1" and set(u.squads) == {"lite", "pro-m", "arcadia"} and u.device_limit == 15
    assert await gw.get_user(999) is None
    assert [x.id for x in await gw.find_users_by_telegram_id(1)] == [501]
    assert await gw.get_subscription_url(501) == "https://sub.example/501"
    assert (await gw.list_squads())["pro"] == "sq-pro"


async def test_update_keeps_manual_squads_and_never_lowers_limit(gw):
    await gw.update_user(501, squads=["pro"], device_limit=10,
                         expire_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    u = await gw.get_user(501)
    assert set(u.squads) == {"pro", "pro-m", "arcadia"}
    assert u.device_limit == 15
    await gw.update_user(501, device_limit=20)
    assert (await gw.get_user(501)).device_limit == 20


async def test_manual_squads_are_never_written(gw):
    with pytest.raises(ValueError):
        await gw.update_user(501, squads=["pro-friend"])
    with pytest.raises(ValueError):
        await gw.create_user("x", telegram_id=2, expire_at=datetime.now(timezone.utc), squads=["arcadia"])


async def test_create_iter_ping(gw):
    u = await gw.create_user("tg_2", telegram_id=2, expire_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                             squads=["standard"], device_limit=5)
    assert u.id and u.squads == ("standard",)
    assert sorted([x.id async for x in gw.iter_users(page_size=1)]) == sorted([501, u.id])
    assert await gw.ping() is True
    gw.fake.healthy = False
    assert await gw.ping() is False


# --- checkout / maintenance ---------------------------------------------------------

async def test_checkout_quote_uses_catalog_prices_only():
    from app.domain.plans import get_plan_price

    bot, _ = make_bot()
    co = build_container(bot, payments=FakePaymentGateway()).checkout
    q = await co.quote(1, "pro", 12)
    assert q.amount_rub == get_plan_price("pro", 12) and q.title == "Pro" and not q.is_legacy
    assert await co.quote(1, "trial", 1) is None
    with patch("app.services.users.get_user_last_plan", AsyncMock(return_value=None)):
        assert await co.quote(1, "basic", 1) is None  # legacy only for its owner
    with patch("app.services.users.get_user_last_plan", AsyncMock(return_value="basic")):
        assert (await co.quote(1, "basic", 1)).is_legacy


async def test_checkout_slot_delegates_to_stream_a_checkout():
    from app.services.checkout import CheckoutServiceImpl

    bot, _ = make_bot()
    c = build_container(bot, payments=FakePaymentGateway())
    assert isinstance(c.checkout._impl(), CheckoutServiceImpl)
    assert c.checkout._impl().d.payments is c.payments


async def test_maintenance_guard_roundtrip():
    r = FakeRedis()
    from app.services.maintenance import RedisMaintenanceGuard

    g = RedisMaintenanceGuard()
    with patch("app.services.cache.get_redis_client", return_value=r):
        assert await g.is_active() is False
        await g.set_active(True, reason="panel down", by=1)
        assert await g.is_active() and await g.reason() == "panel down"
        await g.set_active(False)
        assert await g.is_active() is False
    with patch("app.services.cache.get_redis_client", return_value=None):
        assert await g.is_active() is False


def test_container_is_a_dataclass_of_ports_only():
    from dataclasses import fields

    assert {f.name for f in fields(Container)} == set(PORT_OF_FIELD) | {"bot", "settings"}
