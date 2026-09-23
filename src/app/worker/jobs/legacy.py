"""2.x background tasks wrapped as scheduler jobs, behaviour unchanged.

- subscription_check: SubscriptionChecker._run_once (recovery, expiry
  notifier, reconciler stages; each stage still gated by its TASK_* flag).
- sun718_revert:      Sun718RevertTask._tick_safe.
- broadcast_resume:   resume_unfinished_broadcasts, once at startup.

The task objects are created once per scheduler (the reconciler singleton
lives on the SubscriptionChecker instance, as before).
"""
from __future__ import annotations

from app.logger import logger


class LegacyJobs:
    def __init__(self, bot):
        from app.tasks.subscription_checker import SubscriptionChecker
        from app.tasks.sun718_revert import Sun718RevertTask

        self.bot = bot
        self.checker = SubscriptionChecker(bot, check_interval=3600)
        self.sun718 = Sun718RevertTask(bot, check_interval=3600)

    async def subscription_check(self, ctx) -> None:
        await self.checker._run_once(label=ctx.label)

    async def sun718_revert(self, ctx) -> None:
        await self.sun718._tick_safe(label=ctx.label)

    async def broadcast_resume(self, ctx) -> None:
        try:
            from app.services.broadcast import resume_unfinished_broadcasts

            resumed = await resume_unfinished_broadcasts(self.bot)
            if resumed:
                logger.info(f"Resumed {resumed} unfinished broadcast(s) после рестарта")
        except Exception as _e:
            logger.warning(f"broadcast resume failed: {_e}")
