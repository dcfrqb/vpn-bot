"""Job ``panel_health`` (TASK_PANEL_HEALTH_ENABLED, every 30 s). Owner: C.

One probe of the Remnawave API per run (app.services.maintenance). After
3 failures in a row: one admin notice and, with MAINTENANCE_AUTO_ENABLED,
automatic maintenance mode; cleared by the first successful probe.
The monitor object lives across runs (in-process fallback counter).
"""
from __future__ import annotations

from typing import Any, Optional

_monitor: Optional[Any] = None


def _get_monitor(container: Any):
    global _monitor
    from app.services.maintenance import PanelHealthMonitor

    if _monitor is None or _monitor.remna is not container.remna:
        _monitor = PanelHealthMonitor(container.remna, container.maintenance, container.notifier,
                                      getattr(container, "settings", None))
    return _monitor


async def run(ctx: Any) -> None:
    container = ctx.container
    if container is None:
        from app.container import get_container

        container = get_container()
    await _get_monitor(container).probe()
