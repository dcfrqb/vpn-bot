"""DevicesService and the nightly stale-device cleanup (stream B)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.models import AdminTopic
from app.services import devices as devices_mod
from app.services.devices import PanelDevicesService, cleanup_stale_devices


@pytest.fixture
def svc(gw, repo, redis):
    return PanelDevicesService(gw, repo, settings=SimpleNamespace(DEVICES_UNLINK_ENABLED=True))


@pytest.fixture
def user_with_devices(fake):
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], limit=5)
    fake.add_device(501, "HWID-OLD-00000001", updated="2026-06-01T00:00:00Z")
    fake.add_device(501, "HWID-NEW-00000002", updated="2026-09-22T00:00:00Z")
    fake.add_device(501, "HWID-MID-00000003", updated="2026-08-01T00:00:00Z")
    fake.add_device(501, "HWID-XXX-00000004", updated="2026-09-01T00:00:00Z")
    return fake


async def test_list_newest_first_and_no_account(svc, user_with_devices):
    assert [d.short_id for d in await svc.list_devices(7)] == ["00000002", "00000004", "00000003", "00000001"]
    assert await svc.list_devices(8) == []
    assert not user_with_devices.created


async def test_unlink_three_per_day(svc, user_with_devices, redis):
    await redis.set("status:v1:7", "{}")
    assert await svc.unlinks_left(7) == 3
    for sid in ("00000001", "00000002", "00000003"):
        assert await svc.unlink(7, sid) is True
    assert await svc.unlinks_left(7) == 0
    assert await svc.unlink(7, "00000004") is False
    assert len(user_with_devices.devices[501]) == 1
    assert redis.ttl["devices:unlink:7"] == 24 * 3600
    assert "status:v1:7" not in redis.store  # the status card recounts devices


async def test_unknown_device_does_not_spend_the_limit(svc, user_with_devices):
    assert await svc.unlink(7, "nope") is False
    assert await svc.unlink(7, "") is False
    assert await svc.unlinks_left(7) == 3


async def test_flag_off(gw, repo, redis, user_with_devices):
    off = PanelDevicesService(gw, repo, settings=SimpleNamespace(DEVICES_UNLINK_ENABLED=False))
    assert await off.unlinks_left(7) == 0
    assert await off.unlink(7, "00000001") is False
    assert len(user_with_devices.devices[501]) == 4


async def test_redis_down_refuses_unlink(svc, user_with_devices, redis):
    redis.down = True
    assert await svc.unlink(7, "00000001") is False
    assert len(user_with_devices.devices[501]) == 4


async def test_cleanup_dry_run_counts_only(gw, user_with_devices, notifier, clock):
    user_with_devices.add_user(502, "v", telegram_id=8)
    user_with_devices.add_device(502, "HWID-ZZZ-00000009", updated="2026-01-01T00:00:00Z")
    r = await cleanup_stale_devices(gw, notifier, days=30, dry_run=True, clock=clock)
    assert (r.scanned, r.stale, len(r.users), r.deleted) == (5, 3, 2, 0)
    assert not user_with_devices.deleted_devices
    sent = notifier.to_admins(AdminTopic.PANEL)
    assert len(sent) == 1 and "пробный прогон" in sent[0].text and "Устаревших: 3" in sent[0].text


async def test_cleanup_deletes_and_caps(gw, user_with_devices, notifier, clock, monkeypatch):
    monkeypatch.setattr(devices_mod, "CLEANUP_MAX_DELETES", 1)
    r = await cleanup_stale_devices(gw, notifier, days=30, dry_run=False, clock=clock)
    assert r.deleted == 1 and r.capped and len(user_with_devices.devices[501]) == 3
    r = await cleanup_stale_devices(gw, notifier, days=30, dry_run=False, clock=clock)
    assert r.deleted == 1 and len(user_with_devices.devices[501]) == 2


async def test_cleanup_nothing_stale_is_silent_in_dry_run(gw, fake, notifier, clock):
    fake.add_user(501, "u", telegram_id=7)
    fake.add_device(501, "HWID-NEW-00000002", updated="2026-09-22T00:00:00Z")
    r = await cleanup_stale_devices(gw, notifier, days=30, dry_run=True, clock=clock)
    assert r.stale == 0 and not notifier.sent
