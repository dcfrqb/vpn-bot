"""ProvisioningService (stream B): one entry point, one PATCH, hotfix protections."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.domain.models import Entitlement, EntitlementSource as Src, SubKind
from app.services.provisioning import (
    GrantRefused,
    PanelProvisioningService,
    ProvisioningBusy,
    ProvisioningError,
    compute_target,
    target_squads,
)
from tests.fakes.remnawave import DEFAULT_SQUADS, FakeRemna, FakeRemnaGateway
from tests.panel.conftest import NOW

SQUADS = {**DEFAULT_SQUADS, "grace": "sq-grace", "esp": "sq-esp"}


class RecordingObhod:
    def __init__(self):
        self.granted = []
        self.revoked = []

    async def on_main_granted(self, tg, plan, until, trace):
        self.granted.append((tg, plan, until))

    async def on_main_revoked(self, tg, trace):
        self.revoked.append(tg)


@pytest.fixture
def fake():
    return FakeRemna(squads=SQUADS)


@pytest.fixture
def obhod():
    return RecordingObhod()


@pytest.fixture
def svc(gw, repo, notifier, clock, obhod, redis):
    return PanelProvisioningService(
        gw, repo, notifier=notifier, obhod=obhod, clock=clock,
        settings=SimpleNamespace(GRACE_SQUAD="grace"), late_patch_delay_s=0,
    )


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def trial(**kw):
    return Entitlement(plan_code="standard", source=Src.TRIAL, days=5, **kw)


# ------------------------------------------------------------------ pure rules

def test_compute_target_extends_from_later_of_now_and_current():
    ent = Entitlement(plan_code="pro", source=Src.PAYMENT, days=10)
    assert compute_target(ent, None, NOW) == NOW + timedelta(days=10)
    later = NOW + timedelta(days=3)
    assert compute_target(ent, later, NOW) == later + timedelta(days=10)
    assert compute_target(ent, NOW - timedelta(days=3), NOW) == NOW + timedelta(days=10)
    assert compute_target(ent, None, NOW, months=1) == datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def test_compute_target_never_shortens_and_lifetime():
    far = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    ent = Entitlement(plan_code="pro", source=Src.ADMIN, until=NOW + timedelta(days=1))
    assert compute_target(ent, far, NOW) == far
    assert compute_target(Entitlement(plan_code="pro", source=Src.ADMIN, is_lifetime=True), None, NOW).year == 2099
    assert compute_target(Entitlement(plan_code="pro", source=Src.PROMO, days=5), far, NOW) == far


def test_target_squads_keeps_everything_but_tariff_and_grace():
    names = ["lite", "pro-friend", "arcadia", "us-2", "esp", "grace", "obhod", "lite-m"]
    assert target_squads(names, "pro", grace_squad="grace") == ["us-2", "esp", "obhod", "pro"]
    assert target_squads(names, "pro", grace_squad="grace", clear_grace=False) == ["us-2", "esp", "grace", "obhod", "pro"]


# ------------------------------------------------------------------ creation

async def test_new_user_is_created_only_by_grant(svc, fake, repo, obhod):
    from app.services.accounts import TgUserRow

    repo.users[7] = TgUserRow(7, username="ivan")
    st = await svc.grant(7, trial(), trace_id="t1")
    assert len(fake.created) == 1 and fake.created[0]["telegram_id"] == 7
    uid = fake.created[0]["id"]
    u = fake.users[uid]
    assert fake.squad_names(uid) == ["standard"] and u["hwidDeviceLimit"] == 5
    assert st.active and st.has_panel_user and st.plan_code == "standard"
    assert st.expires_at.date() == (NOW + timedelta(days=5)).date()
    row = await repo.get_subscription(7)
    assert row.active and row.provisioning_state == "synced" and row.remna_user_id == str(uid)
    assert row.config_data["grants"]["trace:t1"]["state"] == "applied"
    assert row.config_data["last_source"] == "trial"
    assert repo.users[7].remna_user_id == str(uid)
    assert obhod.granted == [(7, "standard", st.expires_at)]


async def test_existing_user_is_found_not_duplicated(svc, fake):
    fake.add_user(501, "tg_ivan", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW + timedelta(days=3)))
    await svc.grant(7, Entitlement(plan_code="pro", source=Src.PAYMENT, payment_id=11), trace_id="t", months=1)
    assert not fake.created
    assert fake.squad_names(501) == ["pro"]
    assert fake.users[501]["hwidDeviceLimit"] == 10
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc))
    assert len(fake.patches) == 1  # ONE PATCH: expire + squads + limit


async def test_username_collision_never_takes_someone_elses_account(svc, fake, repo):
    from app.services.accounts import TgUserRow

    repo.users[7] = TgUserRow(7, username="ivan")
    fake.add_user(900, "tg_ivan", telegram_id=999, squads=["pro"], limit=10)  # namesake
    await svc.grant(7, trial(), trace_id="t")
    assert fake.users[900]["telegramId"] == 999 and fake.squad_names(900) == ["pro"]
    assert fake.created and fake.created[-1]["username"] != "tg_ivan"


async def test_panel_outage_on_lookup_creates_nothing(svc, fake):
    fake.fail_lookup_tg = True
    with pytest.raises(ProvisioningError):
        await svc.grant(7, trial(), trace_id="t")
    assert not fake.created


# ------------------------------------------------------------------ hotfix protections

@pytest.mark.parametrize("manual", [["pro-friend"], ["arcadia"], ["premium-m"], ["lite-m", "us-2"]])
async def test_manual_squads_survive_any_grant(svc, fake, manual):
    fake.add_user(501, "u", telegram_id=7, squads=["premium", *manual], limit=15)
    await svc.grant(7, Entitlement(plan_code="lite", source=Src.PAYMENT, payment_id=1), trace_id="t", months=1)
    names = set(fake.squad_names(501))
    assert set(manual) <= names and "lite" in names and "premium" not in names
    assert fake.users[501]["hwidDeviceLimit"] == 15  # never lowered


@pytest.mark.parametrize("limit,foreign,expected", [
    (0, [], 0),              # unlimited, set by hand
    (None, ["us-2"], None),  # NULL with a manual setup: left alone
    (None, [], 10),          # NULL of a bot-created user: plan limit
    (20, [], 20),
    (3, [], 10),
])
async def test_device_limit_policy(svc, fake, limit, foreign, expected):
    fake.add_user(501, "u", telegram_id=7, squads=["lite", *foreign], limit=limit)
    await svc.grant(7, Entitlement(plan_code="pro", source=Src.PAYMENT, payment_id=1), trace_id="t", months=1)
    assert fake.users[501]["hwidDeviceLimit"] == expected


async def test_disabled_user_is_refused_with_one_alert(svc, fake, notifier):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, status="DISABLED")
    for i in range(2):
        with pytest.raises(GrantRefused) as e:
            await svc.grant(7, Entitlement(plan_code="lite", source=Src.PAYMENT, payment_id=i + 1),
                            trace_id="t", months=1)
        assert e.value.reason == "disabled"
    assert not fake.patches and not fake.enabled
    assert len(notifier.to_admins()) == 1


async def test_disabled_user_enabled_when_approved(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, status="DISABLED")
    st = await svc.grant(7, Entitlement(plan_code="lite", source=Src.PAYMENT, payment_id=1), trace_id="t",
                         months=1, enable_if_disabled=True)
    assert fake.enabled == [501] and st.active


async def test_expired_user_is_revived_by_the_date(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, status="EXPIRED",
                  expire=iso(NOW - timedelta(days=10)))
    st = await svc.grant(7, trial(), trace_id="t")
    assert st.active and fake.users[501]["status"] == "ACTIVE" and not fake.enabled


async def test_lifetime_user_is_not_shortened(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], limit=10, expire="2099-12-31T23:59:59Z")
    st = await svc.grant(7, trial(), trace_id="t")
    assert fake.users[501]["expireAt"].startswith("2099") and st.is_lifetime


async def test_unknown_plan_and_manual_squad_refused(svc):
    with pytest.raises(GrantRefused):
        await svc.grant(7, Entitlement(plan_code="nope", source=Src.ADMIN, days=1), trace_id="t")
    with pytest.raises(GrantRefused):
        await svc.grant(7, Entitlement(plan_code="pro", source=Src.ADMIN, days=1, squad="pro-friend"), trace_id="t")
    with pytest.raises(GrantRefused):
        await svc.grant(7, Entitlement(plan_code="pro", source=Src.ADMIN), trace_id="t")
    with pytest.raises(GrantRefused):
        await svc.grant(7, Entitlement(plan_code="pro", source=Src.ADMIN, days=1, sub_kind=SubKind.OBHOD), trace_id="t")


# ------------------------------------------------------------------ idempotency and failures

async def test_same_payment_twice_extends_once(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW))
    ent = Entitlement(plan_code="lite", source=Src.PAYMENT, payment_id=42)
    a = await svc.grant(7, ent, trace_id="x", months=1)
    patches = len(fake.patches)
    b = await svc.grant(7, ent, trace_id="y", months=1)
    assert a.expires_at == b.expires_at and len(fake.patches) == patches


async def test_failed_apply_marks_row_failed_and_retry_uses_same_target(svc, fake, repo):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW + timedelta(days=2)))
    ent = Entitlement(plan_code="lite", source=Src.PAYMENT, payment_id=5)
    fake.fail_squads = True
    with pytest.raises(ProvisioningError):
        await svc.grant(7, ent, trace_id="t", months=1)
    row = await repo.get_subscription(7)
    assert row.provisioning_state == "failed" and row.config_data["grants"]["pay:5"]["state"] == "pending"
    target = row.config_data["grants"]["pay:5"]["target"]
    fake.fail_squads = False
    svc.remna.invalidate_squads()
    await svc.grant(7, ent, trace_id="t2", months=1)
    assert fake.users[501]["expireAt"] == iso(datetime.fromisoformat(target))
    assert (await repo.get_subscription(7)).provisioning_state == "synced"


async def test_patch_that_timed_out_but_landed_counts(svc, fake, gw):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW))
    real_update = fake.update_user

    async def landed_then_timeout(user_id, **kw):
        await real_update(user_id, **kw)
        raise httpx.ReadTimeout("late")

    fake.update_user = landed_then_timeout
    st = await svc.grant(7, trial(), trace_id="t")
    assert st.active


async def test_verify_requires_the_plan_squad(svc, fake, repo):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW))

    async def drops_squads(user_id, **kw):
        kw.pop("activeInternalSquads", None)
        return await FakeRemna.update_user(fake, user_id, **kw)

    fake.update_user = drops_squads
    with pytest.raises(ProvisioningError):
        await svc.grant(7, trial(), trace_id="t")
    assert (await repo.get_subscription(7)).provisioning_state == "failed"


async def test_lock_busy(svc, redis):
    await redis.set("lock:provision:7", "someone", nx=True)
    import app.services.provisioning as p

    old = p.LOCK_WAIT_S
    p.LOCK_WAIT_S = 0
    try:
        with pytest.raises(ProvisioningBusy):
            await svc.grant(7, trial(), trace_id="t")
    finally:
        p.LOCK_WAIT_S = old


async def test_parallel_grants_for_one_user_are_serialized(svc, fake, repo):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW))
    import app.services.provisioning as p

    old = p.LOCK_POLL_S
    p.LOCK_POLL_S = 0.01
    try:
        await asyncio.gather(*[
            svc.grant(7, Entitlement(plan_code="lite", source=Src.PAYMENT, payment_id=i), trace_id=f"t{i}", months=1)
            for i in (1, 2)
        ])
    finally:
        p.LOCK_POLL_S = old
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc))


# ------------------------------------------------------------------ grace, stale link

async def test_grant_clears_grace(svc, fake, repo):
    fake.add_user(501, "u", telegram_id=7, squads=["lite", "grace", "us-2"], limit=2, expire=iso(NOW))
    repo.add_row(7, plan="lite", panel_id=501, until=NOW, grace_until=NOW + timedelta(days=2), grace_state="active")
    await svc.grant(7, Entitlement(plan_code="lite", source=Src.PAYMENT, payment_id=3), trace_id="t", months=1)
    assert set(fake.squad_names(501)) == {"lite", "us-2"}
    row = await repo.get_subscription(7)
    assert row.grace_until is None and row.grace_state is None


async def test_grant_can_keep_grace(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite", "grace"], limit=2, expire=iso(NOW))
    await svc.grant(7, trial(), trace_id="t", clear_grace=False)
    assert "grace" in fake.squad_names(501)


async def test_stale_stored_id_is_cleared_and_lookup_used(svc, fake, repo):
    from app.services.accounts import TgUserRow

    repo.users[7] = TgUserRow(7, remna_user_id="12345678-aaaa")  # legacy uuid
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2)
    await svc.grant(7, trial(), trace_id="t")
    assert repo.users[7].remna_user_id == "501" and not fake.created


# ------------------------------------------------------------------ revoke and credits

async def test_revoke_cuts_to_now_plus_5_minutes(svc, fake, repo, obhod):
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], limit=10, expire=iso(NOW + timedelta(days=20)))
    repo.add_row(7, plan="pro", panel_id=501, until=NOW + timedelta(days=20))
    assert await svc.revoke(7, reason="refund", trace_id="r") is True
    assert fake.users[501]["expireAt"] == iso(NOW + timedelta(minutes=5))
    row = await repo.get_subscription(7)
    assert not row.active and row.provisioning_state == "expired"
    assert obhod.revoked == [7]


@pytest.mark.parametrize("squads,expire", [(["pro-friend"], "2026-12-01T00:00:00Z"), (["pro"], "2099-12-31T23:59:59Z")])
async def test_revoke_never_touches_manual_or_lifetime(svc, fake, notifier, squads, expire):
    fake.add_user(501, "u", telegram_id=7, squads=squads, limit=10, expire=expire)
    assert await svc.revoke(7, reason="refund", trace_id="r") is False
    assert not fake.patches and notifier.to_admins()


async def test_add_days_existing_only_and_once(svc, fake, repo):
    assert await svc.add_days(7, 3, trace_id="bc1") is None and not fake.created
    fake.add_user(501, "u", telegram_id=7, squads=["lite", "pro-m"], limit=2, expire=iso(NOW + timedelta(days=1)))
    new = await svc.add_days(7, 3, trace_id="bc1")
    assert new == NOW + timedelta(days=4)
    assert await svc.add_days(7, 3, trace_id="bc1") == new  # idempotent
    assert len(fake.patches) == 1 and set(fake.patches[0]) == {"id", "expireAt"}
    with pytest.raises(ValueError):
        await svc.add_days(7, 0, trace_id="x")


async def test_add_traffic_and_devices_never_lower(svc, fake, repo):
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], limit=10)
    fake.add_user(600, "tg_7_obhod", squads=["obhod"], limit=10)
    fake.users[600]["trafficLimitBytes"] = 100
    repo.add_row(7, kind="obhod", plan="obhod", panel_id=600, until=NOW + timedelta(days=3))
    assert await svc.add_traffic(7, 50, trace_id="a") == 150
    assert await svc.add_devices(7, 2, trace_id="b") == 12
    fake.users[501]["hwidDeviceLimit"] = 0
    assert await svc.add_devices(7, 2, trace_id="c") == 0
    with pytest.raises(ValueError):
        await svc.add_traffic(7, -1, trace_id="d")


async def test_container_default_is_the_real_service():
    from app.container import build_container
    from tests.fakes.bot import make_bot

    bot, _ = make_bot()
    c = build_container(bot, remna=FakeRemnaGateway())
    assert isinstance(c.provisioning, PanelProvisioningService)
    assert await c.devices.unlinks_left(1) == 0  # DEVICES_UNLINK_ENABLED is off by default


# ------------------------------------------------------------------ grace (stream C, requests/C.md item 2)

def _grace_user(fake, repo, *, status="ACTIVE", paid_days_left=-1, grace_state="active"):
    """User after C's grace start: squads=[grace], 5 GB/day, expireAt = grace end."""
    grace_end = NOW + timedelta(days=2)
    fake.add_user(501, "u", telegram_id=7, squads=["grace"], limit=10, expire=iso(grace_end), status=status)
    fake.users[501]["trafficLimitBytes"] = 5 * 1024 ** 3
    fake.users[501]["trafficLimitStrategy"] = "DAY"
    repo.add_row(7, plan="pro", panel_id=501, active=False, until=NOW + timedelta(days=paid_days_left),
                 grace_until=grace_end, grace_state=grace_state)
    return grace_end


async def test_paid_grant_during_grace_counts_from_the_paid_term(svc, fake, repo):
    _grace_user(fake, repo)
    st = await svc.grant(7, Entitlement(plan_code="pro", source=Src.PAYMENT, payment_id=9), trace_id="t", months=1)
    # paid term ended yesterday -> one month from NOW, not from the grace end (no free days)
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc))
    assert fake.squad_names(501) == ["pro"]  # grace squad gone
    assert fake.users[501]["trafficLimitBytes"] == 0 and fake.users[501]["trafficLimitStrategy"] == "NO_RESET"
    row = await repo.get_subscription(7)
    assert row.grace_until is None and row.grace_state is None and row.active
    assert st.active and st.grace_until is None


async def test_paid_grant_with_paid_days_left_extends_from_them(svc, fake, repo):
    _grace_user(fake, repo, paid_days_left=1)  # grace started early, 1 paid day left
    await svc.grant(7, Entitlement(plan_code="pro", source=Src.PAYMENT, payment_id=9), trace_id="t", months=1)
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc))


@pytest.mark.parametrize("status", ["DISABLED", "EXPIRED"])
async def test_grace_end_user_is_enabled_not_refused(svc, fake, repo, notifier, status):
    _grace_user(fake, repo, status=status, grace_state="ended")
    fake.users[501]["expireAt"] = iso(NOW - timedelta(hours=1))
    st = await svc.grant(7, Entitlement(plan_code="pro", source=Src.PAYMENT, payment_id=9), trace_id="t", months=1)
    assert st.active and fake.users[501]["status"] == "ACTIVE"
    assert not notifier.to_admins()  # not treated as an admin DISABLE
    if status == "DISABLED":
        assert fake.enabled == [501]


async def test_admin_disabled_user_without_grace_is_still_refused(svc, fake, repo):
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], limit=10, status="DISABLED")
    repo.add_row(7, plan="pro", panel_id=501, until=NOW + timedelta(days=3))
    with pytest.raises(GrantRefused):
        await svc.grant(7, Entitlement(plan_code="pro", source=Src.PAYMENT, payment_id=9), trace_id="t", months=1)


async def test_repeat_of_applied_grant_with_panel_down_answers_from_db(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW))
    ent = Entitlement(plan_code="lite", source=Src.PAYMENT, payment_id=77)
    await svc.grant(7, ent, trace_id="x", months=1)
    fake.fail_lookup_tg = fake.fail_get_user = True
    st = await svc.grant(7, ent, trace_id="x", months=1)
    assert st.stale and st.active


# ------------------------------------------------------------------ review money B-1: retry vs a moved panel

def _fail_next_patch(fake):
    real = fake.update_user
    state = {"left": 1}

    async def flaky(user_id, **kw):
        if state["left"]:
            state["left"] -= 1
            raise httpx.ReadTimeout("lost")  # the PATCH never landed
        return await real(user_id, **kw)

    fake.update_user = flaky


def _pay(pid, plan="lite"):
    return Entitlement(plan_code=plan, source=Src.PAYMENT, payment_id=pid)


OCT1 = datetime(2026, 10, 1, tzinfo=timezone.utc)


async def test_retry_after_another_payment_stacks_and_never_loses_a_month(svc, fake, repo):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(OCT1))
    _fail_next_patch(fake)
    with pytest.raises(ProvisioningError):
        await svc.grant(7, _pay(1), trace_id="a", months=1)  # A: pending, target 01.11
    await svc.grant(7, _pay(2), trace_id="b", months=1)       # B: 01.10 -> 01.11
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 11, 1, tzinfo=timezone.utc))
    await svc.grant(7, _pay(1), trace_id="a2", months=1)      # retry of A stacks
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 12, 1, tzinfo=timezone.utc))
    grants = (await repo.get_subscription(7)).config_data["grants"]
    assert grants["pay:1"]["state"] == "applied" and grants["pay:2"]["state"] == "applied"


async def test_retry_after_payment_and_promo_never_shortens(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(OCT1))
    _fail_next_patch(fake)
    with pytest.raises(ProvisioningError):
        await svc.grant(7, _pay(1), trace_id="a", months=1)
    await svc.grant(7, _pay(2), trace_id="b", months=1)                                          # 01.11
    await svc.grant(7, Entitlement(plan_code="lite", source=Src.PROMO, days=10), trace_id="promo")  # 11.11
    await svc.grant(7, _pay(1), trace_id="a2", months=1)
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 12, 11, tzinfo=timezone.utc))


async def test_retry_after_add_days_credit_recomputes(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(OCT1))
    _fail_next_patch(fake)
    with pytest.raises(ProvisioningError):
        await svc.grant(7, _pay(1), trace_id="a", months=1)
    await svc.add_days(7, 3, trace_id="bc:1:7")  # 04.10, no grant record
    await svc.grant(7, _pay(1), trace_id="a2", months=1)
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 11, 4, tzinfo=timezone.utc))


async def test_landed_but_unverified_payment_is_not_granted_twice(svc, fake, repo):
    """A's PATCH landed but the verify and the late probe both failed: the panel
    sits on A's target. B stacks on it, and A's retry adds nothing."""
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(OCT1))
    real_get = fake.get_user_by_id
    calls = {"n": 0}

    async def get_fails_after_patch(user_id):
        if fake.patches and calls["n"] < 2:
            calls["n"] += 1
            raise httpx.ConnectError("verify lost")
        return await real_get(user_id)

    fake.get_user_by_id = get_fails_after_patch
    with pytest.raises(ProvisioningError):
        await svc.grant(7, _pay(1), trace_id="a", months=1)
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 11, 1, tzinfo=timezone.utc))  # it landed
    await svc.grant(7, _pay(2), trace_id="b", months=1)
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 12, 1, tzinfo=timezone.utc))
    patches = len(fake.patches)
    await svc.grant(7, _pay(1), trace_id="a2", months=1)
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert len(fake.patches) == patches  # closed as applied, no write
    assert (await repo.get_subscription(7)).config_data["grants"]["pay:1"]["state"] == "applied"


async def test_retry_with_panel_on_recorded_target_is_idempotent(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(OCT1))
    real_get = fake.get_user_by_id
    calls = {"n": 0}

    async def get_fails_after_patch(user_id):
        if fake.patches and calls["n"] < 2:
            calls["n"] += 1
            raise httpx.ConnectError("verify lost")
        return await real_get(user_id)

    fake.get_user_by_id = get_fails_after_patch
    with pytest.raises(ProvisioningError):
        await svc.grant(7, _pay(1), trace_id="a", months=1)
    await svc.grant(7, _pay(1), trace_id="a2", months=1)
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 11, 1, tzinfo=timezone.utc))


def _lose_next_verify(fake):
    """The next PATCH lands, but its verify and the late probe both fail."""
    real_get = fake.get_user_by_id
    start = len(fake.patches)
    calls = {"n": 0}

    async def get_fails_after_patch(user_id):
        if len(fake.patches) > start and calls["n"] < 2:
            calls["n"] += 1
            raise httpx.ConnectError("verify lost")
        return await real_get(user_id)

    fake.get_user_by_id = get_fails_after_patch


@pytest.mark.parametrize("retry_order", [("a", "b"), ("b", "a")])
async def test_same_base_pending_pair_keeps_both_paid_months(svc, fake, repo, retry_order):
    """Review round 2, N-1: A's PATCH never lands, B (same base 01.10) lands
    unverified. A panel on 01.11 proves one grant, not two: in any retry order
    the user ends on 01.12."""
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(OCT1))
    _fail_next_patch(fake)
    with pytest.raises(ProvisioningError):
        await svc.grant(7, _pay(1), trace_id="a", months=1)  # A: pending, base 01.10, nothing landed
    _lose_next_verify(fake)
    with pytest.raises(ProvisioningError):
        await svc.grant(7, _pay(2), trace_id="b", months=1)  # B: landed 01.11, unverified
    nov1 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert fake.users[501]["expireAt"] == iso(nov1)
    grants = (await repo.get_subscription(7)).config_data["grants"]
    assert grants["pay:1"]["moved"] is True and not grants["pay:2"].get("moved")
    pays = {"a": 1, "b": 2}
    for n, who in enumerate(retry_order):
        await svc.grant(7, _pay(pays[who]), trace_id=f"{who}-retry{n}", months=1)
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 12, 1, tzinfo=timezone.utc))
    grants = (await repo.get_subscription(7)).config_data["grants"]
    assert grants["pay:1"]["state"] == "applied" and grants["pay:2"]["state"] == "applied"
    patches = len(fake.patches)
    await svc.grant(7, _pay(1), trace_id="a-again", months=1)
    await svc.grant(7, _pay(2), trace_id="b-again", months=1)
    assert len(fake.patches) == patches  # both closed, no third month


async def test_same_base_pending_pair_where_neither_landed(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(OCT1))
    for pid in (1, 2):
        _fail_next_patch(fake)
        with pytest.raises(ProvisioningError):
            await svc.grant(7, _pay(pid), trace_id=f"t{pid}", months=1)
    assert fake.users[501]["expireAt"] == iso(OCT1)
    await svc.grant(7, _pay(2), trace_id="t2r", months=1)
    await svc.grant(7, _pay(1), trace_id="t1r", months=1)
    assert fake.users[501]["expireAt"] == iso(datetime(2026, 12, 1, tzinfo=timezone.utc))


def test_retry_target_rules():
    from app.services.provisioning_rules import retry_target

    ent = _pay(1)
    rec = {"target": datetime(2026, 11, 1, tzinfo=timezone.utc).isoformat(), "base": OCT1.isoformat()}
    nov1, dec1 = datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 12, 1, tzinfo=timezone.utc)
    assert retry_target(rec, ent, OCT1, NOW, months=1) == nov1                    # nothing landed
    assert retry_target(rec, ent, nov1, NOW, months=1) == nov1                    # it landed
    assert retry_target({**rec, "moved": True}, ent, nov1, NOW, months=1) == dec1  # another grant moved it
    far = datetime(2027, 3, 1, tzinfo=timezone.utc)
    assert retry_target(rec, ent, far, NOW, months=1) == datetime(2027, 4, 1, tzinfo=timezone.utc)
    legacy = {"target": nov1.isoformat()}  # no base recorded: recompute, never shorten
    assert retry_target(legacy, ent, dec1, NOW, months=1) == datetime(2027, 1, 1, tzinfo=timezone.utc)


# ------------------------------------------------------------------ review money M-3: broadcast credit contract

async def test_broadcast_credit_reaches_the_real_provisioning_service(svc, fake):
    """grants.add_days -> PanelProvisioningService.add_days with the real
    signature (it raised TypeError on every call before the fix)."""
    from app.services.broadcast import BroadcastCredits
    from app.services.grants import add_days
    from tests.growth.fakes import MemoryLedger

    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW + timedelta(days=1)))
    new = await add_days(svc, None, 7, 3, trace_id="bc:1:7")
    assert new == NOW + timedelta(days=4)

    fake.add_user(502, "v", telegram_id=8, squads=["pro"], limit=10, expire=iso(NOW + timedelta(days=2)))
    fake.add_user(503, "w", telegram_id=9, squads=["pro"], limit=10, expire="2099-12-31T23:59:59Z")
    ledger = MemoryLedger()
    credit = BroadcastCredits(provisioning=svc, status=None, ledger=ledger)
    assert await credit(5, 8, 7) == "applied"
    assert fake.users[502]["expireAt"] == iso(NOW + timedelta(days=9))
    assert await credit(5, 8, 7) == "dup"
    assert await credit(5, 9, 7) == "skipped"   # lifetime: nothing to add
    assert await credit(5, 10, 7) == "skipped"  # no panel account: nothing created
    assert fake.users[502]["expireAt"] == iso(NOW + timedelta(days=9))
    assert not fake.created


async def test_failed_credit_patch_can_be_retried(svc, fake):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW + timedelta(days=1)))
    _fail_next_patch(fake)
    with pytest.raises(httpx.ReadTimeout):
        await svc.add_days(7, 3, trace_id="bc:2:7")
    assert await svc.add_days(7, 3, trace_id="bc:2:7") == NOW + timedelta(days=4)


async def test_credit_sweep_alerts_admins_on_failures(monkeypatch):
    from app.services import broadcast as bc

    sent = []

    class N:
        async def notify_admins(self, topic, text, **kw):
            sent.append((topic, text))

    class _Res:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            return _Res([(1,), (2,)])

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "SessionLocal", lambda: _S())
    monkeypatch.setattr("app.container.get_container", lambda: SimpleNamespace(notifier=N()))

    async def credit(bid, uid, days):
        return "failed" if uid == 2 else "applied"

    assert await bc._credit_sweep(3, 5, credit, asyncio.Event()) == 1
    assert len(sent) == 1 and "1 получател" in sent[0][1]


# ------------------------------------------------------------------ review money M-1: rollback of one period

async def test_refund_of_one_month_keeps_earlier_paid_months(svc, fake, repo, obhod):
    until = datetime(2027, 3, 1, tzinfo=timezone.utc)  # 5 paid months + 1 renewal
    fake.add_user(501, "u", telegram_id=7, squads=["pro"], limit=10, expire=iso(until))
    repo.add_row(7, plan="pro", panel_id=501, until=until)
    assert await svc.revoke(7, reason="refund_24h:1", trace_id="rr:1", months=1) is True
    assert fake.users[501]["expireAt"] == iso(datetime(2027, 2, 1, tzinfo=timezone.utc))
    row = await repo.get_subscription(7)
    assert row.active and row.valid_until == datetime(2027, 2, 1, tzinfo=timezone.utc)
    assert obhod.revoked == [] and obhod.granted[-1][2] == datetime(2027, 2, 1, tzinfo=timezone.utc)
    # idempotent per trace
    patches = len(fake.patches)
    assert await svc.revoke(7, reason="refund_24h:1", trace_id="rr:1", months=1) is True
    assert len(fake.patches) == patches


async def test_refund_of_the_only_month_is_a_full_cut(svc, fake, repo, obhod):
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(NOW + timedelta(days=30)))
    repo.add_row(7, plan="lite", panel_id=501, until=NOW + timedelta(days=30))
    res = await svc.rollback(7, months=1, reason="refund", trace_id="refund:rf-1")
    assert res.action == "expired" and fake.users[501]["expireAt"] == iso(NOW + timedelta(minutes=5))
    assert not (await repo.get_subscription(7)).active and obhod.revoked == [7]


async def test_rollback_retry_after_a_new_grant_does_not_erase_it(svc, fake, repo):
    until = datetime(2027, 1, 1, tzinfo=timezone.utc)
    fake.add_user(501, "u", telegram_id=7, squads=["lite"], limit=2, expire=iso(until))
    repo.add_row(7, plan="lite", panel_id=501, until=until)
    _fail_next_patch(fake)
    with pytest.raises(httpx.ReadTimeout):
        await svc.rollback(7, months=1, reason="refund", trace_id="refund:rf-2")  # target 01.12 pending
    await svc.grant(7, _pay(9), trace_id="p9", months=1)  # 01.01 -> 01.02
    res = await svc.rollback(7, months=1, reason="refund", trace_id="refund:rf-2")
    assert res.action == "shortened"
    assert fake.users[501]["expireAt"] == iso(until)  # 01.02 - 1 month, the new payment survives
