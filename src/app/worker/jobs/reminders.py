"""Job ``reminders`` (TASK_REMINDERS_ENABLED): subscription expiry reminders. Owner: C.

Replaces the 2.x expiry_notifier (tasks/expiry_notifier.py). While this job
is enabled, SubscriptionChecker skips its EXPIRY_NOTIFIER stage, so users
never get both.

Windows, in Moscow calendar days between today and the panel expireAt:
  3d  three days before      (key type 3d, same key as 2.x)
  1d  one day before         (new)
  0d  on the expiry day      (key type 0d, same key as 2.x)
  a1d one day after, expired (new)
Sent only between 10:00 and 21:00 MSK; the job runs every 30 minutes, so
every window day gets several chances.

Dedup keys stay 2.x-compatible: ``expiry_notice:<type>:<tg>:<expire UTC date>``
(set by the 2.x notifier and by refunds.suppress_expiry_notices), so a
reminder already sent by 2.1.1 is not sent again after the switch.

Skipped: lifetime, DISABLED (cut by an admin or a refund), obhod accounts
(no telegramId), users with autorenew on, users whose last payment was
refunded, users inside or after a grace period (their expireAt is the grace
end, the grace job talks to them).
Button: «Продлить подписку» -> Period(last plan, last period) when checkout
still sells it to the user, else the plan list.
Redis down -> nothing is sent (no dedupe = spam on every run).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.models import ensure_utc
from app.domain.texts import to_msk
from app.domain.texts import notify as T
from app.logger import logger

FROM_HOUR_MSK = 10
TO_HOUR_MSK = 21  # exclusive: the last run is before 21:00
KEY_PREFIX = "expiry_notice"
# days until expiry (MSK) -> (key type, dedup TTL seconds)
WINDOWS: dict[int, tuple[str, int]] = {
    3: ("3d", 4 * 24 * 3600),
    1: ("1d", 2 * 24 * 3600),
    0: ("0d", 2 * 24 * 3600),
    -1: ("a1d", 3 * 24 * 3600),
}
SEND_DELAY_S = 0.05


def in_send_hours(now: datetime) -> bool:
    return FROM_HOUR_MSK <= to_msk(now).hour < TO_HOUR_MSK


def window_for(expire_at: datetime, now: datetime) -> Optional[tuple[str, int]]:
    days = (to_msk(expire_at).date() - to_msk(now).date()).days
    return WINDOWS.get(days)


def dedup_key(kind: str, telegram_id: int, expire_at: datetime) -> str:
    utc_date = expire_at.astimezone(timezone.utc).date().isoformat()
    return f"{KEY_PREFIX}:{kind}:{int(telegram_id)}:{utc_date}"


async def send_reminders(container: Any, repo: Any = None, now: Optional[datetime] = None) -> dict:
    from app.bot.views.notify import renew_kb
    from app.infra.redis.flags import delete_key, get_value, set_once
    from app.services.events_repo import SqlEventsRepo
    from app.worker.panel_events import renew_target

    now = now or datetime.now(timezone.utc)
    stats = {"checked": 0, "sent": 0, "skipped": 0, "dedup": 0, "errors": 0}
    if not in_send_hours(now):
        stats["outside_hours"] = 1
        return stats
    repo = repo if repo is not None else SqlEventsRepo()

    async for user in container.remna.iter_users():
        if not user.telegram_id or user.expire_at is None:
            continue
        expire_at = ensure_utc(user.expire_at)
        if expire_at.year >= 2099 or expire_at.year < 2020:
            continue
        if (user.status or "").upper() == "DISABLED":
            continue
        win = window_for(expire_at, now)
        if win is None:
            continue
        kind, ttl = win
        tg = int(user.telegram_id)
        stats["checked"] += 1
        key = dedup_key(kind, tg, expire_at)
        if await get_value(key) is not None:
            stats["dedup"] += 1
            continue
        try:
            info = await repo.reminder_info(tg)
        except Exception as e:  # noqa: BLE001 - one user must not stop the run
            stats["errors"] += 1
            logger.warning(f"reminders: tg={tg} info failed ({type(e).__name__})")
            continue
        if info.autorenew or info.refunded or info.is_lifetime or info.grace_state:
            stats["skipped"] += 1
            continue
        got = await set_once(key, "1", ttl=ttl)
        if got is None:
            logger.warning("reminders: Redis unavailable, run stopped (no dedupe)")
            stats["errors"] += 1
            break
        if got is False:
            stats["dedup"] += 1
            continue
        plan, months = await renew_target(container, tg, info)
        sent = await container.notifier.notify_user(
            tg, T.reminder_text(kind, expire_at), reply_markup=renew_kb(plan, months),
        )
        if sent:
            stats["sent"] += 1
            logger.info(f"reminders: {kind} sent to tg={tg}")
        else:
            stats["errors"] += 1
            await delete_key(key)  # retry on the next run
        await asyncio.sleep(SEND_DELAY_S)

    if stats["sent"] or stats["errors"]:
        logger.info(f"reminders: {stats}")
    return stats


async def run(ctx: Any) -> None:
    container = ctx.container
    if container is None:
        from app.container import get_container

        container = get_container()
    await send_reminders(container)
