"""3.0 Foundation: one scheduler loop, leader lock, flags, JOBS registry."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.worker.scheduler import Job, Scheduler, build_jobs
from tests.fakes.clock import FakeClock


class Leader:
    def __init__(self, value=True):
        self.value = value
        self.released = False

    async def ensure(self):
        return self.value

    async def release(self):
        self.released = True


def make(jobs, leader=True, clock=None):
    return Scheduler(jobs, leader=Leader(leader), clock=clock or FakeClock(), tick_s=3600)


async def _drain():
    for _ in range(5):
        await asyncio.sleep(0)


async def boot(s):
    """start() + let the loop do its first tick, then stop the loop (ticks are manual)."""
    await s.start()
    await _drain()
    s._loop_task.cancel()


async def test_runs_due_enabled_jobs_and_respects_interval():
    clock = FakeClock()
    runs = []

    async def work(ctx):
        runs.append(ctx.label)

    s = make([Job("a", work, 60, enabled=lambda: True)], clock=clock)
    await boot(s)
    assert runs == ["startup"]  # first loop tick started it
    assert await s.tick() == []
    clock.advance(30)
    assert await s.tick() == []
    clock.advance(31)
    assert await s.tick() == ["a"]
    await _drain()
    assert runs == ["startup", "periodic"]
    s.stop()


async def test_disabled_job_never_runs_and_flag_is_rechecked():
    state = {"on": False}
    work = AsyncMock()
    s = make([Job("a", work, 60, enabled=lambda: state["on"])])
    await boot(s)
    work.assert_not_awaited()
    state["on"] = True
    assert await s.tick() == ["a"]
    s.stop()


async def test_not_leader_runs_nothing_then_once_job_runs_when_leadership_comes():
    once, periodic = AsyncMock(), AsyncMock()
    s = make([Job("o", once, 0, enabled=lambda: True, once=True),
              Job("p", periodic, 60, enabled=lambda: True)], leader=False)
    await boot(s)
    assert await s.tick() == []
    once.assert_not_awaited()
    periodic.assert_not_awaited()
    s.leader.value = True  # the stale lock of a dead process expired
    assert sorted(await s.tick()) == ["o", "p"]
    await _drain()
    once.assert_awaited_once()
    assert await s.tick() == []  # once means once
    s.stop()


async def test_redis_down_means_leader():
    work = AsyncMock()
    s = make([Job("a", work, 60, enabled=lambda: True)], leader=None)
    assert await s.is_leader() is True


async def test_once_job_awaited_in_start():
    once = AsyncMock()
    s = make([Job("o", once, 0, enabled=lambda: True, once=True)])
    await boot(s)
    once.assert_awaited_once()
    assert await s.tick() == []
    s.stop()


async def test_job_never_overlaps_itself_and_failures_do_not_kill_loop():
    clock = FakeClock()
    gate = asyncio.Event()

    async def slow(ctx):
        await gate.wait()

    async def bad(ctx):
        raise RuntimeError("boom")

    s = make([Job("slow", slow, 1, enabled=lambda: True), Job("bad", bad, 1, enabled=lambda: True)], clock=clock)
    await boot(s)
    clock.advance(5)
    started = await s.tick()
    assert "slow" not in started and "bad" in started  # slow is still running
    await _drain()
    assert s.state["bad"].failures >= 1
    gate.set()
    await _drain()
    clock.advance(5)
    assert "slow" in await s.tick()
    s.stop()


def test_duplicate_job_names_rejected():
    with pytest.raises(ValueError):
        make([Job("a", AsyncMock(), 1), Job("a", AsyncMock(), 1)])


def test_task_flag_gate():
    from app import config

    j = Job("x", AsyncMock(), 60, flag="SUN718_REVERT")
    with patch.object(config.settings, "BACKGROUND_TASKS_ENABLED", True), \
         patch.object(config.settings, "TASK_SUN718_REVERT_ENABLED", True):
        assert j.is_enabled()
    with patch.object(config.settings, "BACKGROUND_TASKS_ENABLED", False), \
         patch.object(config.settings, "TASK_SUN718_REVERT_ENABLED", True):
        assert not j.is_enabled()
    assert not Job("y", AsyncMock(), 60, flag="NO_SUCH_TASK").is_enabled()
    assert not Job("z", AsyncMock(), 60).is_enabled()


EXPECTED_JOBS = {
    # name: (flag, interval_s)
    "payment_recovery": ("RECOVERY", 300),
    "autopay": ("AUTOPAY", 3600),
    "panel_sync": ("PANEL_SYNC", None),
    "device_cleanup": ("DEVICE_CLEANUP", 86400),
    "obhod_lifecycle": ("OBHOD_LIFECYCLE", 86400),
    "reminders": ("REMINDERS", 1800),
    "grace": ("GRACE", 3600),
    "panel_health": ("PANEL_HEALTH", 30),
    "sun718_revert": ("SUN718_REVERT", 3600),
    "broadcast_resume": ("BROADCAST_RESUME", 0),
    "expiry_notifier": (None, 3600),
    "reconciler": ("RECONCILER", 3600),
}


def test_registry_has_every_job_gated_by_its_flag():
    from app.config import settings

    jobs = {j.name: j for j in build_jobs(object())}
    assert set(jobs) == set(EXPECTED_JOBS)
    for name, (flag, interval) in EXPECTED_JOBS.items():
        assert jobs[name].flag == flag, name
        if interval is not None:
            assert jobs[name].interval_s == interval, name
    assert jobs["panel_sync"].interval_s == settings.RECONCILER_INTERVAL_S
    assert jobs["broadcast_resume"].once
    assert not jobs["device_cleanup"].run_at_start and not jobs["obhod_lifecycle"].run_at_start


def test_new_jobs_are_off_by_default_and_2x_jobs_on():
    """First deploy of 3.0 with an unchanged .env runs only what 2.1 ran."""
    from app.config import Settings

    s = Settings(_env_file=None)
    for flag in ("AUTOPAY", "PANEL_SYNC", "DEVICE_CLEANUP", "OBHOD_LIFECYCLE", "REMINDERS", "GRACE",
                 "PANEL_HEALTH"):
        assert getattr(s, f"TASK_{flag}_ENABLED") is False, flag
    for flag in ("RECOVERY", "EXPIRY_NOTIFIER", "RECONCILER", "SUN718_REVERT", "BROADCAST_RESUME"):
        assert getattr(s, f"TASK_{flag}_ENABLED") is True, flag
    assert s.DEVICE_CLEANUP_DRY_RUN is True and s.GRACE_ENABLED is False and s.AUTOPAY_ENABLED is False
    assert s.OBHOD_ORPHAN_DEACTIVATE_ENABLED is False


def test_expiry_notifier_is_muted_while_reminders_run(monkeypatch):
    from app.worker.jobs import legacy

    flags = {"EXPIRY_NOTIFIER": True, "REMINDERS": True}
    monkeypatch.setattr("app.config.task_enabled", lambda n: flags.get(n, False))
    assert legacy.expiry_notifier_enabled() is False
    flags["REMINDERS"] = False
    assert legacy.expiry_notifier_enabled() is True
    flags["EXPIRY_NOTIFIER"] = False
    assert legacy.expiry_notifier_enabled() is False


async def test_start_scheduler_runs_enabled_jobs_all_on():
    from app import config
    from app.worker import scheduler as sched

    with patch.object(config.settings, "BACKGROUND_TASKS_ENABLED", True), \
         patch("app.worker.jobs.broadcast_resume.run", AsyncMock()) as res, \
         patch("app.worker.jobs.sun718_revert.run", AsyncMock()) as sun, \
         patch("app.worker.jobs.recovery.run", AsyncMock()) as rec, \
         patch("app.worker.jobs.legacy.reconciler", AsyncMock()) as recon, \
         patch("app.worker.jobs.legacy.expiry_notifier", AsyncMock()) as exp, \
         patch("app.services.cache.get_redis_client", return_value=None):
        handle = await sched.start_scheduler(object())
        await _drain()
        handle.stop()
    res.assert_awaited_once()
    sun.assert_awaited_once()
    rec.assert_awaited_once()
    recon.assert_awaited_once()
    exp.assert_awaited_once()
