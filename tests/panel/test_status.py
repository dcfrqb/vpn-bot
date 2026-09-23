"""StatusService (stream B): panel first, Redis cache, invalidate, stale fallback, grace."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.services.status import PanelStatusService, build_state, state_from_json, state_to_json
from tests.panel.conftest import NOW


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def svc(gw, repo, clock, redis):
    return PanelStatusService(gw, repo, clock=clock)


async def test_state_from_panel_with_obhod_and_devices(svc, fake, repo):
    fake.add_user(501, "u", telegram_id=7, squads=["pro-friend", "us-2"], limit=5, expire=iso(NOW + timedelta(days=10)))
    fake.add_device(501, "HWID-1111111111")
    fake.add_user(600, "tg_7_obhod", squads=["obhod"], limit=10, expire=iso(NOW + timedelta(days=10)))
    fake.users[600].update(trafficLimitBytes=100, usedTrafficBytes=40)
    repo.add_row(7, plan="basic", panel_id=501, until=NOW, autorenew=True)
    repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10))
    st = await svc.get_state(7)
    assert st.active and st.has_panel_user and st.plan_code == "pro"  # squads win over a stale DB plan
    assert st.expires_at == NOW + timedelta(days=10) and st.days_left(NOW) == 10
    assert st.device_limit == 5 and st.devices_used == 1 and st.autorenew
    assert st.obhod_active and st.obhod_limit_bytes == 100 and st.obhod_used_bytes == 40
    assert st.subscription_url and "https://sub.example/501" == st.subscription_url
    assert "sub.example" not in repr(st)


async def test_no_panel_account_is_not_created(svc, fake):
    st = await svc.get_state(7)
    assert not st.has_panel_user and not st.active and not fake.created


async def test_cache_force_and_invalidate(svc, fake, redis):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW + timedelta(days=1)))
    a = await svc.get_state(7)
    fake.users[501]["expireAt"] = iso(NOW + timedelta(days=30))
    assert (await svc.get_state(7)).expires_at == a.expires_at  # cached
    assert (await svc.get_state(7, force=True)).expires_at == NOW + timedelta(days=30)  # «Обновить»
    fake.users[501]["expireAt"] = iso(NOW + timedelta(days=60))
    await svc.invalidate(7)
    assert (await svc.get_state(7)).expires_at == NOW + timedelta(days=60)


async def test_panel_down_serves_last_good_copy_as_stale(svc, fake, redis):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW + timedelta(days=5)))
    await svc.get_state(7)
    fake.fail_get_user = True
    fake.fail_lookup_tg = True
    st = await svc.get_state(7, force=True)
    assert st.stale and st.active and st.expires_at == NOW + timedelta(days=5)


async def test_panel_down_no_copy_is_db_only(svc, fake, repo):
    repo.add_row(7, plan="standard", panel_id=501, until=NOW + timedelta(days=3))
    fake.fail_lookup_tg = True
    fake.fail_get_user = True
    st = await svc.get_state(7)
    assert st.stale and st.active and st.plan_code == "standard" and not st.has_panel_user


async def test_limited_is_active_disabled_is_not(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, status="LIMITED",
                  expire=iso(NOW + timedelta(days=5)))
    assert (await svc.get_state(7)).active
    fake.users[501]["status"] = "DISABLED"
    assert not (await svc.get_state(7, force=True)).active


async def test_grace_is_not_paid_time(svc, fake, repo):
    grace_end = NOW + timedelta(days=2)
    fake.squads["grace"] = "sq-grace"
    fake.add_user(501, "u", telegram_id=7, squads=["grace"], limit=10, expire=iso(grace_end))
    repo.add_row(7, plan="pro", panel_id=501, active=False, until=NOW - timedelta(days=1),
                 grace_until=grace_end, grace_state="active")
    st = await svc.get_state(7)
    assert not st.active and st.grace_until == grace_end
    assert st.expires_at == NOW - timedelta(days=1) and st.plan_code == "pro"


def test_json_roundtrip():
    st = build_state(7, user=None, main_row=None, now=NOW)
    assert state_from_json(state_to_json(st)) == st
    assert state_from_json({"telegram_id": "x", "fetched_at": "bad"}) is None
