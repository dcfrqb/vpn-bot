"""HWID devices of the user's main panel account (stream B).

``PanelDevicesService`` (DevicesService port):
  - ``list_devices``: newest activity first; no panel account -> [];
  - ``unlink``: by ``DeviceInfo.short_id`` (last 8 chars of the hwid, never
    the full hwid in callbacks); off unless DEVICES_UNLINK_ENABLED; at most
    UNLINKS_PER_DAY successful unlinks per rolling window of 24 h, counted
    from the first one (Redis counter). Redis down -> unlink refused (a family
    rotating slots is exactly what the limit is for);
  - ``unlinks_left``.

``cleanup_stale_devices``: the nightly job body. Devices whose ``updatedAt``
is older than DEVICE_CLEANUP_DAYS are deleted (DEVICE_CLEANUP_DRY_RUN=true
by default: count only). A device is re-registered by the client on its next
connect, so this only frees slots of phones that are gone. The admin gets
the counts via Notifier (topic PANEL).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.domain.models import AdminTopic, DeviceInfo
from app.infra.redis.flags import get_value, incr_counter
from app.logger import logger
from app.services.accounts import AccountsRepo, PanelAccounts, SqlAccountsRepo

UNLINKS_PER_DAY = 3
UNLINK_WINDOW_S = 24 * 3600
UNLINK_KEY = "devices:unlink:{}"
CLEANUP_MAX_DELETES = 500  # safety cap per run
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PanelDevicesService:
    def __init__(self, remna=None, repo: Optional[AccountsRepo] = None, *, settings: Any = None,
                 status: Any = None):
        if remna is None:
            from app.infra.remnawave.gateway import HttpRemnaGateway

            remna = HttpRemnaGateway()
        self.remna = remna
        self.accounts = PanelAccounts(remna, repo or SqlAccountsRepo())
        self._settings = settings
        self._status = status

    @property
    def settings(self):
        if self._settings is None:
            from app.config import settings

            return settings
        return self._settings

    def _enabled(self) -> bool:
        return bool(getattr(self.settings, "DEVICES_UNLINK_ENABLED", False))

    async def list_devices(self, telegram_id: int) -> list[DeviceInfo]:
        user = await self.accounts.find_main(int(telegram_id))
        if user is None:
            return []
        devices = await self.remna.list_devices(user.id)
        return sorted(devices, key=lambda d: d.updated_at or d.created_at or _EPOCH, reverse=True)

    async def _used(self, tg: int) -> Optional[int]:
        raw = await get_value(UNLINK_KEY.format(tg))
        if raw is None:
            return 0
        try:
            return int(raw)
        except ValueError:
            return 0

    async def unlinks_left(self, telegram_id: int) -> int:
        if not self._enabled():
            return 0
        used = await self._used(int(telegram_id))
        return max(0, UNLINKS_PER_DAY - int(used or 0))

    async def unlink(self, telegram_id: int, device_short_id: str) -> bool:
        tg = int(telegram_id)
        if not self._enabled() or not device_short_id:
            return False
        if await self.unlinks_left(tg) <= 0:
            logger.info(f"devices: unlink rate limit reached tg={tg}")
            return False
        user = await self.accounts.find_main(tg)
        if user is None:
            return False
        matches = [d for d in await self.remna.list_devices(user.id) if d.short_id == device_short_id]
        if len(matches) != 1:
            return False  # unknown or ambiguous handle: do nothing
        count = await incr_counter(UNLINK_KEY.format(tg), ttl=UNLINK_WINDOW_S)
        if count is None:
            logger.warning(f"devices: Redis unavailable, unlink refused tg={tg}")
            return False
        if count > UNLINKS_PER_DAY:
            return False  # a parallel press took the last slot
        ok = await self.remna.delete_device(user.id, matches[0].hwid)
        logger.info(f"devices: unlink tg={tg} panel_id={user.id} device=..{device_short_id} ok={ok}")
        if ok:
            await self._invalidate_status(tg)
        return bool(ok)

    async def _invalidate_status(self, tg: int) -> None:
        """The device count on the status card is stale after an unlink."""
        try:
            if self._status is not None:
                await self._status.invalidate(tg)
            else:
                from app.infra.redis import cache
                from app.services.status import FRESH_KEY

                await cache.invalidate(FRESH_KEY.format(tg))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"devices: status invalidate failed tg={tg} ({type(e).__name__})")


# ---------------------------------------------------------------------------
# Nightly stale-device cleanup
# ---------------------------------------------------------------------------


@dataclass
class CleanupReport:
    dry_run: bool
    days: int
    scanned: int = 0
    stale: int = 0
    deleted: int = 0
    failed: int = 0
    users: set = field(default_factory=set)
    capped: bool = False

    def text(self) -> str:
        mode = "пробный прогон, ничего не удалено" if self.dry_run else "удаление"
        lines = [
            f"Чистка устройств ({mode})",
            f"Порог: не заходили {self.days} дн. и больше",
            f"Устройств в панели: {self.scanned}",
            f"Устаревших: {self.stale} (у {len(self.users)} польз.)",
        ]
        if not self.dry_run:
            lines.append(f"Удалено: {self.deleted}, ошибок: {self.failed}")
        if self.capped:
            lines.append(f"Достигнут предел {CLEANUP_MAX_DELETES} удалений за прогон, остальное завтра")
        return "\n".join(lines)


async def _iter_devices(remna):
    """(panel_id, DeviceInfo) for every device: one paged endpoint when the
    gateway has it, else user by user."""
    if hasattr(remna, "iter_all_devices"):
        async for d in remna.iter_all_devices():
            if d.user_id:
                yield d.user_id, d.to_device_info()
        return
    async for user in remna.iter_users():
        for d in await remna.list_devices(user.id):
            yield user.id, d


async def cleanup_stale_devices(remna, notifier, *, days: int, dry_run: bool,
                                clock: Callable[[], datetime] = _now) -> CleanupReport:
    report = CleanupReport(dry_run=dry_run, days=int(days))
    cutoff = clock() - timedelta(days=int(days))
    stale: list[tuple[int, DeviceInfo]] = []
    async for panel_id, dev in _iter_devices(remna):
        report.scanned += 1
        seen = dev.updated_at or dev.created_at
        if seen is not None and seen < cutoff:
            stale.append((panel_id, dev))
            report.users.add(panel_id)
    report.stale = len(stale)
    if not dry_run:
        for panel_id, dev in stale:
            if report.deleted + report.failed >= CLEANUP_MAX_DELETES:
                report.capped = True
                break
            try:
                if await remna.delete_device(panel_id, dev.hwid):
                    report.deleted += 1
                else:
                    report.failed += 1
            except Exception as e:  # noqa: BLE001
                report.failed += 1
                logger.warning(f"device cleanup: delete failed panel_id={panel_id} ({type(e).__name__})")
    logger.info(
        f"device cleanup: dry_run={dry_run} days={days} scanned={report.scanned} stale={report.stale} "
        f"users={len(report.users)} deleted={report.deleted} failed={report.failed}"
    )
    if notifier is not None and (report.stale or not dry_run):
        await notifier.notify_admins(AdminTopic.PANEL, report.text(), disable_notification=True)
    return report
