"""Panel health probe and automatic maintenance mode. Owner: C. No aiogram.

Works over the MaintenanceGuard port (RedisMaintenanceGuard below: one Redis key
``maintenance:state`` with a reason). The automatic mode is the same flag
with a reason starting with ``auto:``, so:
  - the probe only ever clears a flag it set itself (reason ``auto:...``);
    a manual flag set by an admin stays until the admin removes it;
  - an admin who switches an automatic flag off suppresses the probe for
    SUPPRESS_TTL_S (the panel is still down, the admin decided to serve anyway).

Probe (worker job panel_health, every 30 s under TASK_PANEL_HEALTH_ENABLED):
  - RemnaGateway.ping() with a timeout;
  - FAILS_THRESHOLD failures in a row -> one admin notice (PANEL topic) and,
    with MAINTENANCE_AUTO_ENABLED, the flag ``auto:...`` is set;
  - the first success after that -> flag cleared (if automatic) and one
    "panel is back" notice.
Redis down: the counter lives in the process (the scheduler runs on one leader).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.models import AdminTopic
from app.domain.texts import notify as T
from app.logger import logger

AUTO_PREFIX = "auto:"
AUTO_REASON = "auto: панель Remnawave не отвечает"
FAILS_KEY = "maintenance:probe_fails"
DOWN_KEY = "maintenance:panel_down"
SUPPRESS_KEY = "maintenance:auto_suppressed"
FAILS_THRESHOLD = 3
PING_TIMEOUT_S = 10.0
SUPPRESS_TTL_S = 3600
FAILS_TTL_S = 900


def is_auto_reason(reason: Optional[str]) -> bool:
    return bool(reason) and str(reason).startswith(AUTO_PREFIX)


async def suppress_auto(ttl: int = SUPPRESS_TTL_S) -> None:
    from app.infra.redis.flags import set_value

    await set_value(SUPPRESS_KEY, "1", ttl=ttl)


class PanelHealthMonitor:
    def __init__(self, remna: Any, guard: Any, notifier: Any, settings: Any = None):
        self.remna = remna
        self.guard = guard
        self.notifier = notifier
        self._settings = settings
        self._local_fails = 0  # used only when Redis is down

    @property
    def settings(self):
        if self._settings is not None:
            return self._settings
        from app.config import settings

        return settings

    async def _ping(self) -> bool:
        try:
            return bool(await asyncio.wait_for(self.remna.ping(), timeout=PING_TIMEOUT_S))
        except Exception as e:  # noqa: BLE001 - any failure is "down"
            logger.info(f"panel_health: ping failed ({type(e).__name__})")
            return False

    async def probe(self) -> str:
        """One probe. Returns "ok", "recovered", "fail" or "down"."""
        from app.infra.redis.flags import delete_key, get_value, incr_counter, set_once

        if await self._ping():
            self._local_fails = 0
            await delete_key(FAILS_KEY)
            was_down = await get_value(DOWN_KEY)
            cleared = False
            try:
                if await self.guard.is_active() and is_auto_reason(await self.guard.reason()):
                    await self.guard.set_active(False, reason="auto: панель снова отвечает")
                    cleared = True
            except Exception as e:  # noqa: BLE001
                logger.warning(f"panel_health: guard read failed ({type(e).__name__})")
            if was_down or cleared:
                await delete_key(DOWN_KEY)
                await self.notifier.notify_admins(AdminTopic.PANEL, T.ADMIN_PANEL_UP)
                return "recovered"
            return "ok"

        fails = await incr_counter(FAILS_KEY, ttl=FAILS_TTL_S)
        if fails is None:
            self._local_fails += 1
            fails = self._local_fails
        if fails < FAILS_THRESHOLD:
            return "fail"

        auto = bool(getattr(self.settings, "MAINTENANCE_AUTO_ENABLED", False))
        switched = False
        if auto and not await get_value(SUPPRESS_KEY):
            try:
                if not await self.guard.is_active():
                    await self.guard.set_active(True, reason=AUTO_REASON)
                    switched = True
            except Exception as e:  # noqa: BLE001
                logger.warning(f"panel_health: guard write failed ({type(e).__name__})")
        first = await set_once(DOWN_KEY, "1", ttl=7 * 24 * 3600)
        if first is True or (first is None and fails == FAILS_THRESHOLD):
            await self.notifier.notify_admins(
                AdminTopic.PANEL, T.admin_panel_down(int(fails), switched),
                dedup_key="panel_down", dedup_ttl=1800,
            )
        return "down"


# --- MaintenanceGuard port: manual/automatic flag in Redis (moved from shims at cutover) ---

MAINTENANCE_KEY = "maintenance:state"


class RedisMaintenanceGuard:
    """Manual maintenance switch stored in Redis (``maintenance:state`` JSON).

    Redis unavailable -> not active (the bot keeps serving, as in 2.x).
    """

    async def _state(self) -> Optional[dict]:
        from app.infra.redis.flags import get_value

        raw = await get_value(MAINTENANCE_KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            data = {"reason": ""}
        return data if isinstance(data, dict) else {"reason": ""}

    async def is_active(self) -> bool:
        return (await self._state()) is not None

    async def reason(self) -> Optional[str]:
        state = await self._state()
        return (state or {}).get("reason") if state else None

    async def set_active(self, active: bool, *, reason: str = "", by: Optional[int] = None) -> None:
        from app.infra.redis.flags import delete_key, set_value

        if active:
            payload = {"reason": reason, "by": by, "since": datetime.now(timezone.utc).isoformat()}
            await set_value(MAINTENANCE_KEY, json.dumps(payload, ensure_ascii=False))
            logger.warning(f"maintenance ON by={by} reason={reason!r}")
        else:
            await delete_key(MAINTENANCE_KEY)
            logger.warning(f"maintenance OFF by={by}")
