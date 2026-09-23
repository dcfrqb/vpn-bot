"""Job ``grace`` (TASK_GRACE_ENABLED, hourly). Owner: C.

Closes grace periods whose grace_until has passed (app.services.grace) and
tells those users that access is off, with the «Продлить» button. Runs even
when GRACE_ENABLED was switched off later, so no grace is left open.
Starting a grace happens on the user.expired webhook (worker.panel_events).
"""
from __future__ import annotations

from typing import Any


async def close_due(container: Any, repo: Any = None) -> list[int]:
    from app.bot.views.notify import renew_kb
    from app.domain.texts import notify as T
    from app.services.events_repo import SqlEventsRepo
    from app.services.grace import GraceService
    from app.worker.panel_events import renew_target

    repo = repo if repo is not None else SqlEventsRepo()
    service = GraceService(container.remna, repo, provisioning=container.provisioning,
                           settings=getattr(container, "settings", None))
    ended = await service.end_due()
    for tg in ended:
        await container.status.invalidate(tg)
        info = await repo.reminder_info(tg)
        plan, months = await renew_target(container, tg, info)
        await container.notifier.notify_user(
            tg, T.GRACE_ENDED, reply_markup=renew_kb(plan, months),
            dedup_key=f"grace_end:{tg}", dedup_ttl=3 * 24 * 3600,
        )
    return ended


async def run(ctx: Any) -> None:
    container = ctx.container
    if container is None:
        from app.container import get_container

        container = get_container()
    await close_due(container)
