"""Stream C: reminders job (-3d, -1d, 0, +1d; 10:00-21:00 MSK)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.worker.jobs import reminders as R
from tests.events.conftest import NOW
from tests.fakes.bot import markup_rows

MSK = timezone(timedelta(hours=3))


def at_msk(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=MSK).astimezone(timezone.utc)


def exp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sent_to(env):
    return {s.target: s for s in env.notifier.sent if s.kind == "user"}


def data(msg):
    return [b["data"] for row in markup_rows(msg.reply_markup) for b in row]


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    monkeypatch.setattr(R, "SEND_DELAY_S", 0)


@pytest.fixture
def users(env):
    """One user per window + noise. NOW = 23.09.2026 12:00 MSK."""
    p = env.panel
    p.add_user(1, "u3", telegram_id=103, expire=exp(at_msk(2026, 9, 26, 18)))   # -3d
    p.add_user(2, "u1", telegram_id=101, expire=exp(at_msk(2026, 9, 24, 1)))    # -1d (01:00 MSK = 22:00 UTC 23rd)
    p.add_user(3, "u0", telegram_id=100, expire=exp(at_msk(2026, 9, 23, 23)))   # today
    p.add_user(4, "ua", telegram_id=199, expire=exp(at_msk(2026, 9, 22, 10)), status="EXPIRED")  # +1d
    p.add_user(5, "u2", telegram_id=102, expire=exp(at_msk(2026, 9, 25, 12)))   # -2d: no window
    p.add_user(6, "life", telegram_id=150, expire="2099-12-31T00:00:00Z")
    p.add_user(7, "tg_100_obhod", telegram_id=None, expire=exp(at_msk(2026, 9, 23, 23)))
    p.add_user(8, "dis", telegram_id=160, expire=exp(at_msk(2026, 9, 23, 23)), status="DISABLED")
    for tg in (103, 101, 100, 199, 102, 150, 160):
        env.repo.add(tg, has_paid=True, last_plan_code="pro", last_months=3)
    return env


async def test_windows_texts_and_buttons(users):
    env = users
    stats = await R.send_reminders(env.c, env.repo, now=NOW)
    got = sent_to(env)
    assert set(got) == {103, 101, 100, 199}
    assert "через 3 дня" in got[103].text and "26.09.2026" in got[103].text
    assert "завтра" in got[101].text
    assert "последний день" in got[100].text
    assert "закончилась вчера" in got[199].text
    assert all(data(m) == ["pe:pro:3"] for m in got.values())
    assert stats["sent"] == 4


async def test_dedup_keys_are_2x_compatible_and_prevent_resend(users):
    env = users
    # 2.1.1 already sent the 3d notice for this expiry (key uses the expire UTC date)
    env.redis.store["expiry_notice:3d:103:2026-09-26"] = "1"
    await R.send_reminders(env.c, env.repo, now=NOW)
    assert 103 not in sent_to(env)
    assert "expiry_notice:0d:100:2026-09-23" in env.redis.store
    assert "expiry_notice:1d:101:2026-09-23" in env.redis.store  # 01:00 MSK 24th = 23rd UTC
    assert "expiry_notice:a1d:199:2026-09-22" in env.redis.store
    before = len(env.notifier.sent)
    await R.send_reminders(env.c, env.repo, now=NOW + timedelta(minutes=30))
    assert len(env.notifier.sent) == before


@pytest.mark.parametrize("hh,ok", [(9, False), (10, True), (20, True), (21, False), (23, False)])
async def test_only_between_10_and_21_msk(users, hh, ok):
    env = users
    stats = await R.send_reminders(env.c, env.repo, now=at_msk(2026, 9, 23, hh, 59 if hh == 20 else 0))
    assert bool(sent_to(env)) is ok
    assert ("outside_hours" in stats) is (not ok)


async def test_skip_autorenew_refunded_grace_lifetime(users):
    env = users
    env.repo.add(103, autorenew=True)
    env.repo.add(101, refunded=True)
    env.repo.add(100, lifetime=True)
    await env.repo.set_grace(199, until=NOW, state="active")
    await R.send_reminders(env.c, env.repo, now=NOW)
    assert sent_to(env) == {}


async def test_button_falls_back_to_plan_list(users):
    env = users
    env.repo.add(103, has_paid=True, last_plan_code="basic", last_months=1)  # not sold to this user
    env.repo.add(101, has_paid=False)  # trial only
    await R.send_reminders(env.c, env.repo, now=NOW)
    got = sent_to(env)
    assert data(got[103]) == ["n:plans:"] and data(got[101]) == ["n:plans:"]


async def test_failed_send_releases_the_key(users):
    env = users

    async def fail(*a, **k):
        return False

    env.c.notifier.notify_user = fail
    stats = await R.send_reminders(env.c, env.repo, now=NOW)
    assert stats["errors"] == 4 and not [k for k in env.redis.store if k.startswith("expiry_notice:")]


async def test_redis_down_sends_nothing(users):
    env = users
    env.redis.down = True
    await R.send_reminders(env.c, env.repo, now=NOW)
    assert sent_to(env) == {}


def test_texts_have_no_yo_and_no_em_dash():
    from app.domain.texts import notify as T

    for kind in ("3d", "1d", "0d", "a1d"):
        t = T.reminder_text(kind, NOW)
        assert "\u0451" not in t and "\u2014" not in t


async def test_legacy_expiry_notifier_is_muted_when_reminders_on(monkeypatch):
    from app.tasks.subscription_checker import SubscriptionChecker

    flags = {"EXPIRY_NOTIFIER": True, "REMINDERS": True, "RECOVERY": False, "RECONCILER": False}
    monkeypatch.setattr("app.config.task_enabled", lambda n: flags.get(n, False))
    calls = []

    async def fake_expiry(self, prefix):
        calls.append(prefix)

    monkeypatch.setattr(SubscriptionChecker, "_run_expiry", fake_expiry)
    checker = SubscriptionChecker(bot=None)
    await checker._run_once("t")
    assert calls == []
    flags["REMINDERS"] = False
    await checker._run_once("t")
    assert len(calls) == 1
