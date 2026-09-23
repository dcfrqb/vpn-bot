"""Obhod lifecycle job and the live package gate (stream B, 06 H4)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.domain.plans import obhod_base_limit_bytes
from app.services.obhod import ObhodLifecycle
from tests.panel.conftest import NOW


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def life(gw, repo, notifier, clock):
    return ObhodLifecycle(gw, repo, notifier=notifier, clock=clock)


def obhod_user(fake, uid=600, *, status="ACTIVE", days=10, limit=100):
    fake.add_user(uid, f"tg_{uid}_obhod", squads=["obhod"], limit=10, status=status,
                  expire=iso(NOW + timedelta(days=days)))
    fake.users[uid]["trafficLimitBytes"] = limit


async def test_expired_obhod_is_deactivated(life, fake, repo):
    obhod_user(fake, status="EXPIRED", days=-1)
    repo.add_row(7, plan="pro", panel_id=501, until=NOW + timedelta(days=5))
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW - timedelta(days=1))
    r = await life.run()
    assert r.deactivated == 1 and not repo.subs[row.id].active and fake.disabled == [600]


async def test_orphan_is_kept_and_reported_once(life, fake, repo, notifier):
    obhod_user(fake)
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10))
    await life.run()
    await life.run()
    assert repo.subs[row.id].active and not fake.disabled
    assert len([s for s in notifier.to_admins() if "без основной" in s.text]) == 1


async def test_main_inactive_and_dead_turns_obhod_off(life, fake, repo):
    obhod_user(fake)
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], status="EXPIRED", expire=iso(NOW - timedelta(days=1)))
    repo.add_row(7, plan="pro", panel_id=501, active=False, until=NOW - timedelta(days=1))
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10))
    r = await life.run()
    assert r.deactivated == 1 and not repo.subs[row.id].active


async def test_main_inactive_but_live_in_panel_is_kept(life, fake, repo, notifier):
    obhod_user(fake)
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], expire=iso(NOW + timedelta(days=30)))
    repo.add_row(7, plan="pro", panel_id=501, active=False, until=NOW - timedelta(days=1))
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10))
    r = await life.run()
    assert r.kept_manual == 1 and repo.subs[row.id].active and notifier.to_admins()


async def test_main_in_grace_is_not_a_live_pro(life, fake, repo):
    obhod_user(fake)
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], expire=iso(NOW + timedelta(days=2)))
    repo.add_row(7, plan="pro", panel_id=501, active=False, until=NOW - timedelta(days=1), grace_state="active")
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10))
    await life.run()
    assert not repo.subs[row.id].active


async def test_expired_package_resets_the_cap_only(life, fake, repo):
    obhod_user(fake, limit=500 * 1024 ** 3)
    repo.add_row(7, plan="pro", panel_id=501, until=NOW + timedelta(days=10))
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], expire=iso(NOW + timedelta(days=10)))
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10),
                       config_data={"package": "obhod_500", "package_until": (NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat(),
                                    "package_limit_bytes": 1})
    r = await life.run()
    assert r.packages_expired == 1
    assert fake.users[600]["trafficLimitBytes"] == obhod_base_limit_bytes()
    assert set(fake.patches[0]) <= {"id", "trafficLimitBytes", "trafficLimitStrategy"}
    cfg = repo.subs[row.id].config_data
    assert "package" not in cfg and cfg["package_history"][0]["package"] == "obhod_500"
    assert (await life.run()).packages_expired == 0  # idempotent


async def test_active_package_is_kept(life, fake, repo):
    obhod_user(fake, limit=7)
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], expire=iso(NOW + timedelta(days=10)))
    repo.add_row(7, plan="pro", panel_id=501, until=NOW + timedelta(days=10))
    repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10),
                 config_data={"package": "obhod_500", "package_until": (NOW + timedelta(days=3)).isoformat()})
    await life.run()
    assert fake.users[600]["trafficLimitBytes"] == 7 and not fake.patches


@pytest.mark.parametrize("status,days,ok", [("ACTIVE", 5, True), ("LIMITED", 5, True), ("EXPIRED", -1, False),
                                            ("DISABLED", 5, False), ("ACTIVE", -1, False)])
async def test_package_gate_is_live(life, fake, repo, status, days, ok):
    obhod_user(fake, status=status, days=days)
    repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=days))
    assert await life.package_gate(7) is ok


async def test_package_gate_refuses_when_panel_down_or_no_row(life, fake, repo):
    assert await life.package_gate(7) is False
    obhod_user(fake)
    repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=5))
    fake.fail_get_user = True
    assert await life.package_gate(7) is False
