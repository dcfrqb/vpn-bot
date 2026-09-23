"""PromoEngine: built-ins, races, record-first rollback, table codes, audience, gifts."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.models import AdminTopic, EntitlementSource, PromoOutcome
from app.services.promo import PromoCodeSpec

TG = 900000501


# ----------------------------------------------------------------- trial


async def test_trial_grants_standard_5_days_and_writes_trial_row(engine_parts):
    engine, repo, prov, status, notifier, _ = engine_parts
    assert await engine.trial_available(TG)
    r = await engine.start_trial(TG)
    assert r.applied and r.plan_code == "standard" and r.days == 5
    ent = prov.calls[0][1]
    assert ent.source is EntitlementSource.TRIAL and ent.days == 5
    assert TG in repo.trials and repo.applied("trial")
    assert not await engine.trial_available(TG)
    assert (await engine.start_trial(TG)).outcome is PromoOutcome.ALREADY_USED
    assert notifier.to_admins(AdminTopic.PROMO)


async def test_trial_refused_with_active_subscription(engine_parts):
    engine, repo, prov, status, *_ = engine_parts
    status.set(TG, active=True, plan_code="pro", expires_at=datetime.now(timezone.utc) + timedelta(days=9))
    assert not await engine.trial_available(TG)
    assert (await engine.start_trial(TG)).outcome is PromoOutcome.NOT_ELIGIBLE
    assert not prov.calls and not repo.payments


async def test_trial_flag_off(engine_parts):
    engine, _, prov, _, _, settings = engine_parts
    settings.PROMO_TRIAL_ENABLED = False
    assert (await engine.start_trial(TG)).outcome is PromoOutcome.DISABLED
    assert not await engine.trial_available(TG)
    assert not prov.calls


@pytest.mark.parametrize("redis_down", [False, True])
async def test_five_parallel_trials_give_one_grant(engine_parts, redis, redis_down):
    engine, repo, prov, *_ = engine_parts
    prov.delay = 0.01
    redis.down = redis_down  # lock fails open -> the record is the only guard
    results = await asyncio.gather(*(engine.start_trial(TG) for _ in range(5)))
    applied = [r for r in results if r.applied]
    assert len(applied) == 1
    assert prov.effective == 1 and len(prov.calls) == 1
    assert {r.outcome for r in results} - {PromoOutcome.APPLIED} <= {PromoOutcome.BUSY, PromoOutcome.ALREADY_USED}


async def test_grant_failure_rolls_back_record_and_retry_works(engine_parts):
    engine, repo, prov, *_ = engine_parts
    prov.fail = True
    assert (await engine.start_trial(TG)).outcome is PromoOutcome.ERROR
    assert not repo.payments and not repo.trials
    prov.fail = False
    assert (await engine.start_trial(TG)).applied


async def test_record_error_never_grants(engine_parts):
    engine, repo, prov, *_ = engine_parts
    repo.fail_record = True
    assert (await engine.redeem(TG, "solokhin")).outcome is PromoOutcome.ERROR
    assert not prov.calls


async def test_solokhin_premium_15_once(engine_parts):
    engine, repo, prov, *_ = engine_parts
    r = await engine.redeem(TG, "/Solokhin")
    assert r.applied and r.plan_code == "premium" and r.days == 15
    assert (await engine.redeem(TG, "solokhin")).outcome is PromoOutcome.ALREADY_USED
    assert len(prov.calls) == 1


# ----------------------------------------------------------------- sun718


async def test_sun718_no_subscription_gives_pro_without_revert(engine_parts):
    engine, repo, prov, status, notifier, _ = engine_parts
    r = await engine.redeem(TG, "sun718")
    assert r.applied and r.plan_code == "pro" and r.days == 5
    meta = repo.payments[f"promo_sun718_{TG}"]["meta"]
    assert "revert_at" not in meta and meta["was_active"] is False
    assert any("SUN718" in s.text for s in notifier.to_admins(AdminTopic.PROMO))


async def test_sun718_active_pro_extends_without_revert(engine_parts):
    engine, repo, prov, status, *_ = engine_parts
    status.set(TG, active=True, plan_code="pro", expires_at=datetime.now(timezone.utc) + timedelta(days=10))
    r = await engine.redeem(TG, "sun718")
    assert r.applied and r.expires_at > datetime.now(timezone.utc) + timedelta(days=14)
    assert "revert_at" not in repo.payments[f"promo_sun718_{TG}"]["meta"]


async def test_sun718_active_non_pro_schedules_revert(engine_parts):
    engine, repo, prov, status, *_ = engine_parts
    status.set(TG, active=True, plan_code="standard", expires_at=datetime.now(timezone.utc) + timedelta(days=10))
    r = await engine.redeem(TG, "sun718")
    assert r.applied
    meta = repo.payments[f"promo_sun718_{TG}"]["meta"]
    assert meta["pre_promo_plan"] == "standard" and meta["revert_completed"] is False
    revert_at = datetime.fromisoformat(meta["revert_at"])
    assert timedelta(days=4, hours=23) < revert_at - datetime.utcnow() <= timedelta(days=5)


async def test_sun718_lifetime_refused_and_nothing_written(engine_parts):
    engine, repo, prov, status, notifier, _ = engine_parts
    status.set(TG, active=True, plan_code="premium", expires_at=datetime(2099, 12, 31, tzinfo=timezone.utc))
    r = await engine.redeem(TG, "sun718")
    assert r.outcome is PromoOutcome.NOT_ELIGIBLE and r.plan_code == "lifetime"
    assert not repo.payments and not prov.calls
    assert notifier.to_admins(AdminTopic.PROMO)


async def test_sun718_repeat_is_refused_with_alert(engine_parts):
    engine, repo, prov, status, notifier, _ = engine_parts
    await engine.redeem(TG, "sun718")
    n = len(notifier.to_admins())
    assert (await engine.redeem(TG, "sun718")).outcome is PromoOutcome.ALREADY_USED
    assert len(notifier.to_admins()) == n + 1 and len(prov.calls) == 1


# ----------------------------------------------------------------- table codes


async def _code(engine, **kw):
    spec = PromoCodeSpec(**{"code": "spring", "days": 7, "plan_code": "standard", **kw})
    return await engine.create_code(spec, created_by=1)


async def test_codes_flag_off(engine_parts):
    engine, _, prov, _, _, settings = engine_parts
    await _code(engine)
    settings.PROMO_CODES_ENABLED = False
    assert (await engine.redeem(TG, "spring")).outcome is PromoOutcome.DISABLED
    assert not await engine.is_known_code("spring")
    assert not prov.calls


async def test_code_rewards_go_into_the_entitlement(engine_parts):
    engine, repo, prov, *_ = engine_parts
    await _code(engine, traffic_gb=50, devices=3)
    r = await engine.redeem(TG, "SPRING")
    assert r.applied and r.days == 7
    ent = prov.calls[0][1]
    assert ent.plan_code == "standard" and ent.days == 7 and ent.device_limit == 3
    assert ent.traffic_limit_bytes == 50 * 1024 ** 3


async def test_days_code_extends_current_plan_of_an_active_user(engine_parts):
    engine, _, prov, status, *_ = engine_parts
    status.set(TG, active=True, plan_code="pro", expires_at=datetime.now(timezone.utc) + timedelta(days=3))
    await _code(engine)
    assert (await engine.redeem(TG, "spring")).applied
    assert prov.calls[0][1].plan_code == "pro"


async def test_plan_code_gives_its_plan(engine_parts):
    engine, _, prov, status, *_ = engine_parts
    status.set(TG, active=True, plan_code="lite", expires_at=datetime.now(timezone.utc) + timedelta(days=3))
    await _code(engine, kind="plan", plan_code="pro")
    assert (await engine.redeem(TG, "spring")).applied
    assert prov.calls[0][1].plan_code == "pro"


@pytest.mark.parametrize("audience,active,paid,ok", [
    ("new", False, False, True),
    ("new", True, False, False),
    ("new", False, True, False),
    ("existing", False, False, False),
    ("existing", True, False, True),
    ("existing", False, True, True),
    ("any", False, False, True),
    ("any", True, True, True),
    ("all", True, False, True),   # 2.x word -> any
    ("paid", False, True, True),  # 2.x word -> existing
])
async def test_audience_rules(engine_parts, audience, active, paid, ok):
    engine, repo, prov, status, *_ = engine_parts
    if active:
        status.set(TG, active=True, plan_code="standard", expires_at=datetime.now(timezone.utc) + timedelta(days=3))
    if paid:
        repo.paid[TG] = "standard"
    await _code(engine, audience=audience)
    r = await engine.redeem(TG, "spring")
    assert r.applied is ok
    if not ok:
        assert r.outcome is PromoOutcome.NOT_ELIGIBLE and not prov.calls


async def test_per_user_limit_and_max_uses(engine_parts):
    engine, *_ = engine_parts
    await _code(engine, max_uses=2, per_user_limit=1)
    assert (await engine.redeem(1001, "spring")).applied
    assert (await engine.redeem(1001, "spring")).outcome is PromoOutcome.ALREADY_USED
    assert (await engine.redeem(1002, "spring")).applied
    assert (await engine.redeem(1003, "spring")).outcome is PromoOutcome.EXHAUSTED


async def test_expired_and_inactive_codes(engine_parts):
    engine, *_ = engine_parts
    row = await _code(engine, valid_until=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert (await engine.redeem(TG, "spring")).outcome is PromoOutcome.EXPIRED
    await _code(engine, code="off1")
    off = await engine.repo.get_code("off1")
    await engine.set_active(off.id, False)
    assert (await engine.redeem(TG, "off1")).outcome is PromoOutcome.DISABLED
    assert row is not None
    assert (await engine.redeem(TG, "nosuch")).outcome is PromoOutcome.NOT_FOUND


@pytest.mark.parametrize("redis_down", [False, True])
async def test_parallel_redemptions_respect_max_uses(engine_parts, redis, redis_down):
    engine, repo, prov, *_ = engine_parts
    redis.down = redis_down
    prov.delay = 0.005
    await _code(engine, max_uses=2)
    results = await asyncio.gather(*(engine.redeem(2000 + i, "spring") for i in range(6)))
    assert sum(r.applied for r in results) == 2 and prov.effective == 2


@pytest.mark.parametrize("redis_down", [False, True])
async def test_same_user_parallel_code_redemption_once(engine_parts, redis, redis_down):
    engine, repo, prov, *_ = engine_parts
    redis.down = redis_down
    prov.delay = 0.005
    await _code(engine)
    results = await asyncio.gather(*(engine.redeem(TG, "spring") for _ in range(5)))
    assert sum(r.applied for r in results) == 1 and prov.effective == 1


async def test_failed_grant_releases_the_use(engine_parts):
    engine, repo, prov, *_ = engine_parts
    await _code(engine, max_uses=1)
    prov.fail = True
    assert (await engine.redeem(TG, "spring")).outcome is PromoOutcome.ERROR
    assert (await engine.repo.get_code("spring")).uses == 0
    prov.fail = False
    assert (await engine.redeem(TG, "spring")).applied


async def test_create_code_validation(engine_parts):
    engine, *_ = engine_parts
    for bad in (dict(code="trial"), dict(code="g_x1"), dict(days=0), dict(audience="vip"), dict(plan_code="gold"),
                dict(code="a b")):
        with pytest.raises(ValueError):
            await _code(engine, **bad)
    assert await _code(engine) is not None
    assert await _code(engine) is None  # duplicate


# ----------------------------------------------------------------- gifts


async def test_gift_single_use_and_idempotent_creation(engine_parts):
    engine, repo, prov, status, notifier, _ = engine_parts
    code = await engine.create_gift(777, "pro", 3, payment_id=55)
    assert code.startswith("g_") and await engine.create_gift(777, "pro", 3, payment_id=55) == code
    assert await engine.is_known_code(code)
    r = await engine.redeem(TG, code)
    assert r.applied and r.plan_code == "pro" and r.days == 91 and r.months == 3
    assert prov.calls[0][1].source is EntitlementSource.GIFT
    assert (await engine.redeem(TG + 1, code)).outcome is PromoOutcome.ALREADY_USED
    assert (await engine.redeem(TG, code)).outcome is PromoOutcome.ALREADY_USED
    assert len(prov.calls) == 1
    assert [s for s in notifier.sent if s.kind == "user" and s.target == 777]


async def test_gift_parallel_claims_one_winner(engine_parts):
    engine, repo, prov, *_ = engine_parts
    prov.delay = 0.005
    code = await engine.create_gift(777, "standard", 1)
    results = await asyncio.gather(*(engine.redeem(3000 + i, code) for i in range(5)))
    assert sum(r.applied for r in results) == 1 and prov.effective == 1


async def test_gifts_flag_and_unsellable_gift(engine_parts):
    engine, _, prov, _, _, settings = engine_parts
    with pytest.raises(ValueError):
        await engine.create_gift(1, "trial", 1)
    code = await engine.create_gift(1, "lite", 1)
    settings.GIFTS_ENABLED = False
    assert (await engine.redeem(TG, code)).outcome is PromoOutcome.DISABLED
    assert not await engine.is_known_code(code)
    assert not prov.calls


async def test_is_known_code(engine_parts):
    engine, _, _, _, _, settings = engine_parts
    assert await engine.is_known_code("trial") and await engine.is_known_code("sun718")
    assert not await engine.is_known_code("login_abc")
    assert not await engine.is_known_code("unknown")
    await _code(engine)
    assert await engine.is_known_code("spring")
    settings.PROMO_SUN718_ENABLED = False
    assert not await engine.is_known_code("sun718")


# ----------------------------------------------------------------- review money M-4: never downgrade


async def test_lite_gift_extends_an_active_pro_subscriber_on_pro(engine_parts):
    engine, _, prov, status, *_ = engine_parts
    status.set(TG, active=True, plan_code="pro", expires_at=datetime.now(timezone.utc) + timedelta(days=150))
    code = await engine.create_gift(777, "lite", 1, payment_id=91)
    r = await engine.redeem(TG, code)
    assert r.applied and prov.calls[0][1].plan_code == "pro" and prov.calls[0][1].months == 1


async def test_plan_code_never_downgrades_but_upgrades(engine_parts):
    engine, _, prov, status, *_ = engine_parts
    status.set(TG, active=True, plan_code="pro", expires_at=datetime.now(timezone.utc) + timedelta(days=40))
    await _code(engine, kind="plan", plan_code="lite")
    assert (await engine.redeem(TG, "spring")).applied
    assert prov.calls[0][1].plan_code == "pro"


async def test_legacy_premium_keeps_its_plan_on_a_standard_gift(engine_parts):
    engine, _, prov, status, *_ = engine_parts
    status.set(TG, active=True, plan_code="premium", expires_at=datetime.now(timezone.utc) + timedelta(days=40))
    code = await engine.create_gift(777, "standard", 1, payment_id=92)
    assert (await engine.redeem(TG, code)).applied
    assert prov.calls[0][1].plan_code == "premium"


async def test_gift_to_a_lifetime_user_is_refused_and_stays_valid(engine_parts):
    engine, _, prov, status, *_ = engine_parts
    status.set(TG, active=True, plan_code="pro", is_lifetime=True)
    code = await engine.create_gift(777, "pro", 1, payment_id=93)
    assert (await engine.redeem(TG, code)).outcome is PromoOutcome.NOT_ELIGIBLE
    assert not prov.calls
    assert (await engine.redeem(TG + 1, code)).applied  # still redeemable by someone else


def test_plan_for_recipient_rules():
    from app.domain.models import SubscriptionState
    from app.services.promo import plan_for_recipient

    def st(plan, active=True):
        return SubscriptionState(telegram_id=1, active=active, plan_code=plan)

    assert plan_for_recipient(st(None, active=False), "pro") == "pro"
    assert plan_for_recipient(st("lite", active=False), "standard") == "standard"  # expired: the offered plan
    assert plan_for_recipient(st("lite"), "pro") == "pro"
    assert plan_for_recipient(st("pro"), "lite") == "pro"
    assert plan_for_recipient(st("standard"), "standard") == "standard"
    assert plan_for_recipient(st("basic"), "pro") == "pro"          # clear upgrade
    assert plan_for_recipient(st("basic"), "lite") == "basic"       # fewer devices
    assert plan_for_recipient(st("premium"), "pro") == "premium"    # 15 devices -> 10: not clear


async def test_refunded_gift_code_stops_working(engine_parts):
    """Review money m-3: the refund webhook switches the code off."""
    engine, repo, prov, *_ = engine_parts
    code = await engine.create_gift(777, "pro", 1, payment_id=94)
    row = await repo.get_code(code)
    await repo.set_active(row.id, False)
    assert (await engine.redeem(TG, code)).outcome is PromoOutcome.EXPIRED
    assert not prov.calls


async def test_unknown_codes_are_rate_limited_but_builtins_are_not(engine_parts):
    """Security m-1: no dictionary guessing of admin codes."""
    engine, _, prov, *_ = engine_parts
    await _code(engine)  # "spring"
    for i in range(10):
        assert (await engine.redeem(TG, f"guess{i}")).outcome is PromoOutcome.NOT_FOUND
    assert (await engine.redeem(TG, "spring")).outcome is PromoOutcome.RATE_LIMITED
    assert (await engine.redeem(TG + 1, "spring")).applied  # per user
    assert (await engine.redeem(TG, "trial")).outcome is not PromoOutcome.RATE_LIMITED


def test_gift_codes_are_masked_in_logs():
    from app.services.promo import mask_code

    assert mask_code("g_abcdefghijkl") == "g_ab..."
    assert mask_code("spring") == "spring"
