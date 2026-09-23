"""panel_sync reconciler (stream B, 06 M2): all users, pull-forward only, never writes the panel."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.domain.models import AdminTopic
from app.services.panel_sync import PanelSync
from tests.panel.conftest import NOW


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def sync(gw, repo, notifier, clock):
    return PanelSync(gw, repo, notifier=notifier, clock=clock, settings=SimpleNamespace(GRACE_SQUAD="grace"))


async def test_pull_forward_manual_extension(sync, fake, repo):
    fake.add_user(22, "friend", telegram_id=1, squads=["pro-friend"], expire="2099-03-26T00:00:00Z")
    fake.add_user(87, "manual", telegram_id=2, squads=["lite"], expire=iso(NOW + timedelta(days=13)))
    a = repo.add_row(1, plan="basic", panel_id=22, until=NOW - timedelta(days=89))
    b = repo.add_row(2, plan="lite", panel_id=87, until=NOW - timedelta(days=17))
    r = await sync.run()
    assert r.pulled_forward == 2
    assert repo.subs[a.id].valid_until.year == 2099 and repo.subs[a.id].is_lifetime
    assert repo.subs[b.id].valid_until == NOW + timedelta(days=13) and repo.subs[b.id].active
    assert not fake.patches and not fake.disabled and not fake.enabled  # never writes the panel


async def test_expired_in_both_closes_row(sync, fake, repo):
    fake.add_user(5, "x", telegram_id=5, squads=["lite"], status="EXPIRED", expire=iso(NOW - timedelta(days=2)))
    row = repo.add_row(5, plan="lite", panel_id=5, until=NOW - timedelta(days=2))
    r = await sync.run()
    assert r.deactivated == 1 and not repo.subs[row.id].active and repo.subs[row.id].provisioning_state == "expired"


async def test_shortfall_is_reported_never_written(sync, fake, repo, notifier):
    fake.add_user(5, "x", telegram_id=5, squads=["lite"], expire=iso(NOW + timedelta(days=1)))
    fake.add_user(6, "y", telegram_id=6, squads=["lite"], status="EXPIRED", expire=iso(NOW - timedelta(days=1)))
    r1 = repo.add_row(5, plan="lite", panel_id=5, until=NOW + timedelta(days=20))
    r2 = repo.add_row(6, plan="lite", panel_id=6, until=NOW + timedelta(days=20))
    r = await sync.run()
    assert r.shortfall == 2 and repo.subs[r1.id].valid_until == NOW + timedelta(days=20)
    assert repo.subs[r2.id].active and not fake.patches
    assert "панель не трогаю" in notifier.to_admins(AdminTopic.PANEL)[0].text


async def test_skips_in_flight_disabled_missing_legacy_and_grace(sync, fake, repo):
    fake.squads["grace"] = "sq-grace"
    fake.add_user(1, "a", telegram_id=1, squads=["lite"], expire=iso(NOW + timedelta(days=40)))
    fake.add_user(2, "b", telegram_id=2, squads=["lite"], status="DISABLED", expire=iso(NOW + timedelta(days=40)))
    fake.add_user(4, "d", telegram_id=4, squads=["grace"], expire=iso(NOW + timedelta(days=2)))
    fake.add_user(5, "e", telegram_id=5, squads=["lite"], expire=iso(NOW + timedelta(days=2)))
    repo.add_row(1, plan="lite", panel_id=1, until=NOW, state="pending")
    repo.add_row(2, plan="lite", panel_id=2, until=NOW)
    repo.add_row(3, plan="lite", panel_id=3, until=NOW)
    repo.add_row(9, plan="lite", until=NOW, remna_user_id="aaaa-bbbb")
    repo.add_row(4, plan="pro", panel_id=4, until=NOW - timedelta(days=1))  # grace squad on the panel
    repo.add_row(5, plan="pro", panel_id=5, until=NOW - timedelta(days=1), grace_state="active")
    before = {k: v.valid_until for k, v in repo.subs.items()}
    r = await sync.run()
    assert (r.skipped_in_flight, r.disabled, r.missing, r.legacy_id, r.grace) == (1, 1, 1, 1, 2)
    assert {k: v.valid_until for k, v in repo.subs.items()} == before


async def test_walks_every_row_and_every_panel_page(sync, fake, repo, monkeypatch):
    import app.services.panel_sync as ps

    monkeypatch.setattr(ps, "PAGE_SIZE", 2)
    for i in range(1, 8):
        fake.add_user(i, f"u{i}", telegram_id=i, squads=["lite"], expire=iso(NOW + timedelta(days=30)))
        repo.add_row(i, plan="lite", panel_id=i, until=NOW)
    from app.services import accounts

    real = accounts.iter_all_subscriptions

    def small_pages(repo_, **kw):
        kw["page"] = 3
        return real(repo_, **kw)

    monkeypatch.setattr(ps, "iter_all_subscriptions", small_pages)
    r = await sync.run()
    assert r.panel_users == 7 and r.rows == 7 and r.pulled_forward == 7


async def test_partial_listing_aborts_without_db_writes(sync, fake, repo, notifier):
    fake.add_user(5, "x", telegram_id=5, squads=["lite"], status="EXPIRED", expire=iso(NOW - timedelta(days=2)))
    row = repo.add_row(5, plan="lite", panel_id=5, until=NOW - timedelta(days=2))

    async def broken(size=50, start=0):
        raise httpx.ConnectError("down")

    fake.get_users = broken
    saves = repo.saves
    r = await sync.run()
    assert r.aborted and repo.saves == saves and repo.subs[row.id].active
    assert "прервана" in notifier.to_admins()[0].text
