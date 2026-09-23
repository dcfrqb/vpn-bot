"""Job ``device_cleanup`` (stream B): nightly stale HWID device cleanup.

Registered in scheduler.build_jobs with interval 86400 and flag
DEVICE_CLEANUP (TASK_DEVICE_CLEANUP_ENABLED, default off). Deletes devices
not seen for DEVICE_CLEANUP_DAYS; DEVICE_CLEANUP_DRY_RUN=true (default) only
counts. The admin gets the counts (topic PANEL).
"""
from __future__ import annotations


async def run(ctx) -> None:
    from app.config import settings
    from app.services.devices import cleanup_stale_devices

    container = ctx.container
    await cleanup_stale_devices(
        container.remna,
        container.notifier,
        days=int(settings.DEVICE_CLEANUP_DAYS),
        dry_run=bool(settings.DEVICE_CLEANUP_DRY_RUN),
    )
