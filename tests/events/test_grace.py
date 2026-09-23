"""Stream C: grace period («льготный» squad), GRACE_ENABLED behind the ProvisioningService port."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.services.events_repo import GRACE_ACTIVE, GRACE_ENDED
from app.services.grace import GIB, GraceService, in_grace
from app.worker.jobs import grace as grace_job
from tests.events.conftest import NOW

TG, PID = 700001, 1500


@pytest.fixture
def g(env):
    env.settings.GRACE_ENABLED = True
    env.panel.add_user(PID, "tg_700001", telegram_id=TG, squads=["pro"], limit=10,
                       expire="2026-09-23T08:00:00Z", status="EXPIRED")
    env.repo.add(TG, panel_id=PID, valid_until=NOW - timedelta(hours=1), active=False, has_paid=True,
                 last_plan_code="pro", last_months=1)
    env.service = GraceService(env.c.remna, env.repo, provisioning=env.c.provisioning,
                               settings=env.settings, clock=lambda: NOW)
    return env


async def _user(env):
    return await env.c.remna.get_user(PID)


async def test_start_sets_grace_squad_daily_traffic_and_three_days(g):
    until = await g.service.start(TG, await _user(g))
    assert until == NOW + timedelta(days=3)
    u = g.panel.users[PID]
    assert g.panel.squad_names(PID) == ["grace"]
    assert u["trafficLimitBytes"] == 5 * GIB and u["trafficLimitStrategy"] == "DAY"
    assert u["hwidDeviceLimit"] == 10  # unchanged
    assert u["status"] == "ACTIVE" and u["expireAt"].startswith("2026-09-26")
    sub = g.repo.subs[TG]
    assert sub["grace_state"] == GRACE_ACTIVE and sub["grace_until"] == until
    assert sub["valid_until"] == NOW - timedelta(hours=1)  # grace is never paid time
    assert in_grace(until, GRACE_ACTIVE, NOW) and not in_grace(until, GRACE_ENDED, NOW)


@pytest.mark.parametrize("setup,reason", [
    (lambda e: setattr(e.settings, "GRACE_ENABLED", False), "disabled"),
    (lambda e: setattr(e.settings, "GRACE_SQUAD", ""), "no_squad"),
    (lambda e: setattr(e.settings, "GRACE_SQUAD", "pro-friend"), "squad_is_manual"),
    (lambda e: e.repo.add(TG, panel_id=PID, has_paid=False), "never_paid"),
    (lambda e: e.repo.add(TG, panel_id=PID, has_paid=True, refunded=True), "refunded"),
    (lambda e: e.repo.add(TG, panel_id=PID, has_paid=True, lifetime=True), "lifetime"),
])
async def test_not_eligible(g, setup, reason):
    setup(g)
    decision = await g.service.eligible(TG, await _user(g))
    assert not decision.ok and decision.reason == reason
    assert await g.service.start(TG, await _user(g)) is None
    assert g.panel.patches == []


@pytest.mark.parametrize("manual", ["pro-m", "premium-friend", "arcadia"])
async def test_users_with_manual_squads_never_get_grace(g, manual):
    g.panel.add_user(PID, "tg_700001", telegram_id=TG, squads=["pro", manual], limit=10, status="EXPIRED")
    assert (await g.service.eligible(TG, await _user(g))).reason == "manual_squad"
    assert await g.service.start(TG, await _user(g)) is None and g.panel.patches == []


async def test_only_one_grace_per_expiry(g):
    assert await g.service.start(TG, await _user(g))
    assert (await g.service.eligible(TG, await _user(g))).reason == "already_active"


async def test_refused_while_provisioning_is_the_2x_placeholder(g):
    from app.services.shims import LegacyProvisioningService

    g.service.provisioning = LegacyProvisioningService()
    assert (await g.service.eligible(TG, await _user(g))).reason == "provisioning_not_ready"


async def test_panel_error_rolls_back_the_db_mark(g):
    async def boom(*a, **k):
        raise ConnectionError("panel down")

    g.c.remna.update_user = boom
    with pytest.raises(ConnectionError):
        await g.service.start(TG, await _user(g))
    assert g.repo.subs[TG]["grace_state"] is None and g.repo.subs[TG]["grace_until"] is None


async def test_end_due_marks_ended_and_notifies(g):
    until = await g.service.start(TG, await _user(g))
    g.panel.users[PID]["status"] = "EXPIRED"  # the panel expired it at expireAt
    later = until + timedelta(minutes=5)
    g.service.clock = lambda: later
    assert await g.service.end_due() == [TG]
    assert g.repo.subs[TG]["grace_state"] == GRACE_ENDED
    assert g.panel.disabled == []  # already expired by the panel, stays payable
    assert await g.service.end_due() == []


async def test_end_disables_when_panel_still_active(g):
    until = await g.service.start(TG, await _user(g))
    g.service.clock = lambda: until + timedelta(minutes=5)
    assert await g.service.end_due() == [TG]
    assert g.panel.disabled == [PID]


async def test_paid_during_grace_is_not_cut(g):
    until = await g.service.start(TG, await _user(g))
    g.panel.users[PID]["expireAt"] = (NOW + timedelta(days=33)).strftime("%Y-%m-%dT%H:%M:%SZ")
    g.service.clock = lambda: until + timedelta(minutes=5)
    assert await g.service.end_due() == []
    assert g.repo.subs[TG]["grace_state"] is None and g.panel.disabled == []


async def test_clear_is_what_provisioning_calls_after_payment(g):
    await g.service.start(TG, await _user(g))
    await g.service.clear(TG)
    assert g.repo.subs[TG]["grace_state"] is None and g.repo.subs[TG]["grace_until"] is None


async def test_grace_job_closes_and_sends_renew_button(g):
    until = await g.service.start(TG, await _user(g))
    g.panel.users[PID]["status"] = "EXPIRED"
    g.repo.subs[TG]["grace_until"] = NOW - timedelta(minutes=1)  # the job uses the real clock
    g.panel.users[PID]["expireAt"] = (NOW - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ended = await grace_job.close_due(g.c, g.repo)
    assert ended == [TG] and until
    msg = [s for s in g.notifier.sent if s.kind == "user"][0]
    assert "Льготный период закончился" in msg.text
    assert msg.reply_markup.inline_keyboard[0][0].callback_data == "pe:pro:1"
    assert g.c.status.invalidated == [TG]


async def test_grace_never_writes_valid_until_or_calls_grant(g):
    await g.service.start(TG, await _user(g))
    assert all(c[0] != "mark_paid" for c in g.repo.calls)
    assert g.repo.subs[TG]["valid_until"] == NOW - timedelta(hours=1)
