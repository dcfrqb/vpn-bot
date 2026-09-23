"""Job ``panel_sync`` (stream B): DB <- panel reconciler over all users.

Registered in scheduler.build_jobs with interval settings.RECONCILER_INTERVAL_S
and flag PANEL_SYNC (TASK_PANEL_SYNC_ENABLED, default off). Logic:
app.services.panel_sync. Never writes the panel.
"""
from __future__ import annotations


async def run(ctx) -> None:
    from app.services.panel_sync import PanelSync

    container = ctx.container
    await PanelSync(container.remna, notifier=container.notifier).run()
