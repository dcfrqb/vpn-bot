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


@pytest.fixture
def life_deactivating(gw, repo, notifier, clock):
    """OBHOD_ORPHAN_DEACTIVATE_ENABLED=true (not the default)."""
    return ObhodLifecycle(gw, repo, notifier=notifier, clock=clock, deactivate_orphans=True)


def obhod_user(fake, uid=600, *, status="ACTIVE", days=10, limit=100):
    fake.add_user(uid, f"tg_{uid}_obhod", squads=["obhod"], limit=10, status=status,
                  expire=iso(NOW + timedelta(days=days)))
    fake.users[uid]["trafficLimitBytes"] = limit


async def test_expired_obhod_of_a_live_pro_is_marked_inactive_without_panel_write(life, fake, repo):
    obhod_user(fake, status="EXPIRED", days=-1)
    repo.add_row(7, plan="pro", panel_id=501, until=NOW + timedelta(days=5))
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW - timedelta(days=1))
    r = await life.run()
    assert r.deactivated == 1 and not repo.subs[row.id].active
    assert fake.disabled == [] and not fake.patches  # already expired on the panel: no panel write


def _snapshot(fake, repo, row):
    return dict(fake.users[600]), repo.subs[row.id]


async def test_orphan_without_main_is_left_untouched_and_reported_once(life, fake, repo, notifier):
    """Owner decision 23.09.2026: orphans are report-only by default."""
    obhod_user(fake)
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10),
                       config_data={"package": "obhod_500",
                                    "package_until": (NOW - timedelta(hours=1)).replace(tzinfo=None).isoformat()})
    before = _snapshot(fake, repo, row)
    r1 = await life.run()
    r2 = await life.run()
    assert r1.orphans == 1 and r1.orphan_reasons == {"no_main": 1} and r1.deactivated == 0
    assert r1.packages_expired == 0 and r2.orphans == 1
    assert _snapshot(fake, repo, row) == before  # no DB write, not even the package expiry
    assert not fake.disabled and not fake.patches
    reports = [s for s in notifier.to_admins() if "без основного Pro" in s.text]
    assert len(reports) == 1 and "не трогаю" in reports[0].text


async def test_orphan_with_inactive_main_is_left_untouched(life, fake, repo, notifier):
    obhod_user(fake)
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], status="EXPIRED", expire=iso(NOW - timedelta(days=1)))
    repo.add_row(7, plan="pro", panel_id=501, active=False, until=NOW - timedelta(days=1))
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10))
    r = await life.run()
    assert r.orphans == 1 and r.orphan_reasons == {"main_inactive": 1} and r.deactivated == 0
    assert repo.subs[row.id].active and not fake.disabled and not fake.patches


async def test_orphan_with_non_pro_main_is_left_untouched(life, fake, repo):
    obhod_user(fake)
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], expire=iso(NOW + timedelta(days=30)))
    repo.add_row(7, plan="lite", panel_id=501, until=NOW + timedelta(days=30))
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10))
    r = await life.run()
    assert r.orphan_reasons == {"main_not_pro": 1} and repo.subs[row.id].active and not fake.disabled


async def test_orphans_are_turned_off_only_with_the_flag(life_deactivating, fake, repo):
    obhod_user(fake)
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], status="EXPIRED", expire=iso(NOW - timedelta(days=1)))
    repo.add_row(7, plan="pro", panel_id=501, active=False, until=NOW - timedelta(days=1))
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10))
    r = await life_deactivating.run()
    assert r.deactivated == 1 and r.orphans_deactivated == 1 and not repo.subs[row.id].active
    assert fake.disabled == [600]


def test_orphan_flag_defaults_to_report_only():
    from app.config import Settings

    assert Settings(_env_file=None).OBHOD_ORPHAN_DEACTIVATE_ENABLED is False
    assert ObhodLifecycle(object(), object()).deactivate_orphans is False


async def test_main_inactive_but_live_in_panel_is_kept(life, fake, repo, notifier):
    obhod_user(fake)
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], expire=iso(NOW + timedelta(days=30)))
    repo.add_row(7, plan="pro", panel_id=501, active=False, until=NOW - timedelta(days=1))
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10))
    r = await life.run()
    assert r.kept_manual == 1 and repo.subs[row.id].active and notifier.to_admins()


async def test_main_in_grace_is_not_a_live_pro(life, life_deactivating, fake, repo):
    obhod_user(fake)
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], expire=iso(NOW + timedelta(days=2)))
    repo.add_row(7, plan="pro", panel_id=501, active=False, until=NOW - timedelta(days=1), grace_state="active")
    row = repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=10))
    assert (await life.run()).orphan_reasons == {"main_inactive": 1} and repo.subs[row.id].active
    await life_deactivating.run()
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
