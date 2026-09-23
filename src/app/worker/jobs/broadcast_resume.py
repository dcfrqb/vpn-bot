"""Job broadcast_resume (stream E): restart unfinished broadcasts after a restart.

Runs once at startup on the leader. Broadcasts with credit_days get the
container's ProvisioningService for their credits. Gate:
TASK_BROADCAST_RESUME_ENABLED (2.x flag, default on). Registered in
scheduler.build_jobs by an orchestrator commit (impl/requests/E.md):

    Job("broadcast_resume", broadcast_resume.run, 0, flag="BROADCAST_RESUME", once=True)
"""
from __future__ import annotations

from app.logger import logger


async def run(ctx) -> None:
    from app.services.broadcast import credits_from_container, resume_unfinished_broadcasts

    c = ctx.container
    if c is None:
        from app.container import get_container

        try:
            c = get_container()
        except RuntimeError:
            c = None
    resumed = await resume_unfinished_broadcasts(ctx.bot, credit=credits_from_container(c))
    if resumed:
        logger.info(f"broadcast_resume: {resumed} broadcast(s) resumed")
