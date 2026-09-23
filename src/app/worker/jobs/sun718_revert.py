"""Job sun718_revert (stream E): put the tariff squad back after the /sun718 Pro bonus.

Hourly. Replaces the 2.x Sun718RevertTask; logic in
app.services.referral.Sun718Reverter (ports only: RemnaGateway, Notifier,
StatusService). Gate: TASK_SUN718_REVERT_ENABLED (2.x flag, default on).
Registered in scheduler.build_jobs by an orchestrator commit (impl/requests/E.md):

    Job("sun718_revert", sun718_revert.run, 3600, flag="SUN718_REVERT")
"""
from __future__ import annotations

from app.logger import logger


async def run(ctx) -> None:
    from app.services.referral import Sun718Reverter

    c = ctx.container
    if c is None:
        from app.container import get_container

        c = get_container()
    done = await Sun718Reverter(remna=c.remna, notifier=c.notifier, status=c.status).run()
    if done:
        logger.info(f"sun718_revert ({ctx.label}): {done} reverted")
