"""The one background scheduler of release 3.0. FROZEN seam.

One asyncio loop ticks every ``tick_s`` seconds. On each tick, if this
process holds the Redis leader lock (``scheduler:leader``), every due and
enabled job is started in its own task; a job never overlaps itself.

Gates, checked on every tick (so a flag flip needs no code change):
  - BACKGROUND_TASKS_ENABLED (master) and TASK_<FLAG>_ENABLED via
    app.config.task_enabled(flag), or a job-specific ``enabled`` callable;
  - leader lock: Redis down -> run anyway (2.x had a single bot container
    and no lock; a debug bot must be started with BACKGROUND_TASKS_ENABLED=false).

Jobs:
  - ``once=True``   : awaited inside ``start()`` before the loop; if this process
                      is not the leader yet, it runs once on the first tick
                      where it becomes leader.
  - ``run_at_start``: first run on the first tick, then every ``interval_s``.

2.x difference: legacy tasks slept ``interval`` AFTER each run; here the next
run is due ``interval_s`` after the previous START (hourly jobs, negligible).

To add a job: a module in app/worker/jobs/ with ``async def run(ctx)``, a
TASK_<NAME>_ENABLED setting (default False) in the owner's config section and
one ``Job(...)`` line in ``build_jobs`` (orchestrator commit).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from app.logger import logger

DEFAULT_TICK_S = 30.0
LEADER_KEY = "scheduler:leader"
LEADER_TTL_S = 90


@dataclass
class JobContext:
    bot: Any
    container: Any
    label: str  # "startup" | "periodic"


@dataclass(frozen=True)
class Job:
    name: str
    run: Callable[[JobContext], Awaitable[None]]
    interval_s: float
    flag: Optional[str] = None  # task_enabled(flag); None -> ``enabled`` required
    enabled: Optional[Callable[[], bool]] = None
    run_at_start: bool = True
    once: bool = False

    def is_enabled(self) -> bool:
        if self.enabled is not None:
            return bool(self.enabled())
        if self.flag is None:
            return False
        from app.config import task_enabled

        return task_enabled(self.flag)


@dataclass
class _JobState:
    next_run: float = 0.0
    task: Optional[asyncio.Task] = None
    runs: int = 0
    failures: int = 0


def build_jobs(bot: Any, container: Any = None) -> list[Job]:
    """The JOBS registry: every background job of the bot, in start order."""
    from app.tasks.subscription_checker import SubscriptionChecker
    from app.worker.jobs.legacy import LegacyJobs

    legacy = LegacyJobs(bot)
    return [
        # 2.x, wrapped as-is (default ON, as in 2.1.1)
        Job("subscription_check", legacy.subscription_check, 3600,
            enabled=SubscriptionChecker.any_stage_enabled),
        Job("sun718_revert", legacy.sun718_revert, 3600, flag="SUN718_REVERT"),
        Job("broadcast_resume", legacy.broadcast_resume, 0, flag="BROADCAST_RESUME", once=True),
        # 3.0 jobs are appended here by their streams (default OFF):
        #   A: autopay            flag="AUTOPAY"
        #   B: device_cleanup     flag="DEVICE_CLEANUP"
        #   B: obhod_lifecycle    flag="OBHOD_LIFECYCLE"
        #   B: panel_sync         flag="PANEL_SYNC"   interval=settings.RECONCILER_INTERVAL_S
        #   C: reminders          flag="REMINDERS"
        #   C: grace              flag="GRACE"
        #   C: panel_health       flag="PANEL_HEALTH"
    ]


class Scheduler:
    def __init__(
        self,
        jobs: list[Job],
        *,
        bot: Any = None,
        container: Any = None,
        tick_s: float = DEFAULT_TICK_S,
        leader: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        names = [j.name for j in jobs]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate job names: {names}")
        self.jobs = list(jobs)
        self.bot = bot
        self.container = container
        self.tick_s = float(tick_s)
        if leader is None:
            from app.infra.redis.locks import LeaderLock

            leader = LeaderLock(LEADER_KEY, LEADER_TTL_S)
        self.leader = leader
        self.clock = clock
        self.state: dict[str, _JobState] = {j.name: _JobState() for j in self.jobs}
        self._loop_task: Optional[asyncio.Task] = None
        self._running = False

    # ------------------------------------------------------------------ gates

    async def is_leader(self) -> bool:
        got = await self.leader.ensure()
        if got is None:
            return True  # Redis unavailable: behave like 2.x (single process)
        return bool(got)

    # ------------------------------------------------------------------- run

    async def _run_job(self, job: Job, label: str) -> None:
        st = self.state[job.name]
        st.runs += 1
        try:
            await job.run(JobContext(bot=self.bot, container=self.container, label=label))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - one job must not kill the loop
            st.failures += 1
            logger.exception(f"scheduler: job {job.name} failed: {type(e).__name__}")

    async def start(self) -> "Scheduler":
        from app.config import settings

        if not settings.BACKGROUND_TASKS_ENABLED:
            logger.warning("BACKGROUND_TASKS_ENABLED=false: фоновые задачи НЕ запущены")
        for job in self.jobs:
            if not job.is_enabled():
                logger.warning(f"scheduler: задача {job.name} выключена")
        leader = await self.is_leader()
        now = self.clock()
        for job in self.jobs:
            st = self.state[job.name]
            if job.once:
                if leader and job.is_enabled():
                    await self._run_job(job, "startup")
                    st.next_run = float("inf")
                else:
                    # Not leader yet (e.g. the previous process died holding the
                    # lock): run it on the first tick where we become leader.
                    st.next_run = now
            else:
                st.next_run = now if job.run_at_start else now + job.interval_s
        self._running = True
        self._loop_task = asyncio.create_task(self._loop())
        logger.info(f"scheduler: started, jobs={[j.name for j in self.jobs]} tick={self.tick_s}s")
        return self

    async def tick(self) -> list[str]:
        """One scheduling pass. Returns names of jobs started (for tests)."""
        started: list[str] = []
        if not await self.is_leader():
            return started
        now = self.clock()
        for job in self.jobs:
            st = self.state[job.name]
            if now < st.next_run:
                continue
            if st.task is not None and not st.task.done():
                continue  # still running: never overlap a job with itself
            if not job.is_enabled():
                continue  # re-checked every tick; runs as soon as it is enabled
            label = "startup" if st.runs == 0 else "periodic"
            st.next_run = float("inf") if job.once else now + job.interval_s
            st.task = asyncio.create_task(self._run_job(job, label), name=f"job:{job.name}")
            started.append(job.name)
        return started

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.exception(f"scheduler: tick failed: {type(e).__name__}")
            try:
                await asyncio.sleep(self.tick_s)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        """Sync stop (called from shutdown paths that are not async-aware)."""
        self._running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
        for st in self.state.values():
            if st.task and not st.task.done():
                st.task.cancel()
        try:
            asyncio.get_running_loop().create_task(self.leader.release())
        except RuntimeError:
            pass


async def start_scheduler(bot: Any, container: Any = None, **kwargs: Any) -> Scheduler:
    return await Scheduler(build_jobs(bot, container), bot=bot, container=container, **kwargs).start()
