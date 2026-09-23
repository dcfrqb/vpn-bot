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


def test_registry_wraps_2x_tasks():
    jobs = {j.name: j for j in build_jobs(object())}
    assert list(jobs) == ["subscription_check", "sun718_revert", "broadcast_resume"]
    assert jobs["subscription_check"].interval_s == 3600 and jobs["sun718_revert"].flag == "SUN718_REVERT"
    assert jobs["broadcast_resume"].once and jobs["broadcast_resume"].flag == "BROADCAST_RESUME"


async def test_legacy_subscription_job_calls_run_once_with_label():
    from app.worker.jobs.legacy import LegacyJobs
    from app.worker.scheduler import JobContext

    lj = LegacyJobs(object())
    with patch.object(lj.checker, "_run_once", AsyncMock()) as run_once, \
         patch.object(lj.sun718, "_tick_safe", AsyncMock()) as tick:
        await lj.subscription_check(JobContext(bot=None, container=None, label="periodic"))
        await lj.sun718_revert(JobContext(bot=None, container=None, label="startup"))
    run_once.assert_awaited_once_with(label="periodic")
    tick.assert_awaited_once_with(label="startup")


async def test_background_delegates_to_scheduler_all_on():
    from app import config
    from app.tasks import background

    with patch.object(config.settings, "BACKGROUND_TASKS_ENABLED", True), \
         patch("app.worker.jobs.legacy.LegacyJobs.subscription_check", AsyncMock()) as chk, \
         patch("app.worker.jobs.legacy.LegacyJobs.sun718_revert", AsyncMock()) as sun, \
         patch("app.worker.jobs.legacy.LegacyJobs.broadcast_resume", AsyncMock()) as res, \
         patch("app.services.cache.get_redis_client", return_value=None):
        handle = await background.start_background_tasks(object())
        await _drain()
        handle.stop()
    res.assert_awaited_once()
    chk.assert_awaited_once()
    sun.assert_awaited_once()
