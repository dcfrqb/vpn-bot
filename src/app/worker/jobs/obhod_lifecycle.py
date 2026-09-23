"""Job ``obhod_lifecycle`` (stream B): daily obhod check against the panel.

Registered in scheduler.build_jobs with interval 86400 and flag
OBHOD_LIFECYCLE (TASK_OBHOD_LIFECYCLE_ENABLED, default off). Logic:
app.services.obhod.ObhodLifecycle.
"""
from __future__ import annotations


async def run(ctx) -> None:
    from app.services.obhod import ObhodLifecycle

    container = ctx.container
    await ObhodLifecycle(container.remna, notifier=container.notifier).run()
