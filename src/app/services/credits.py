"""Mixins of PanelProvisioningService (stream B), split out to keep modules small.

RevokeMixin.revoke: full cut for a refund (expireAt = now + 5 min, row
inactive, obhod off); lifetime and manual-squad users are never cut.
CreditsMixin: add_days / add_traffic / add_devices for EXISTING panel
accounts. Never create an account, never lower anything, idempotent per
trace_id (Redis marker).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Optional

from app.domain.models import Entitlement, EntitlementSource, SubKind
from app.logger import logger
from app.services.accounts import numeric_panel_id
from app.services.provisioning_rules import CREDIT_MARKER_TTL_S, REVOKE_GRACE, compute_target
from app.services.remna_tariff import is_manual_squad_name


class RevokeMixin:

    async def revoke(self, telegram_id: int, *, sub_kind: SubKind = SubKind.MAIN, reason: str,
                     trace_id: str) -> bool:
        tg = int(telegram_id)
        if SubKind(sub_kind) is SubKind.OBHOD:
            await self.obhod.on_main_revoked(tg, trace_id)
            await self._invalidate(tg)
            return True
        lock = await self._lock(tg)
        try:
            user = await self.accounts.find_main(tg)
            row = await self.repo.get_subscription(tg, SubKind.MAIN)
            if user is None:
                return False
            names = await self._squad_names(user)
            manual = [n for n in names if is_manual_squad_name(n)]
            is_lifetime = bool(user.expire_at and user.expire_at.year >= 2099)
            if is_lifetime or manual:
                await self._alert(
                    "Отзыв доступа пропущен: подписка навсегда или ручной сквад.\n"
                    f"Telegram ID: {tg}\nПричина: {reason}\nРешите вручную в панели.",
                    dedup_key=f"revoke_skipped:{tg}:{trace_id}",
                )
                return False
            now = self.clock()
            new_expire = now + REVOKE_GRACE
            if user.expire_at is None or user.expire_at > new_expire:
                await self.remna.update_user(user.id, expire_at=new_expire, current=user)
            if row is not None:
                cfg = dict(row.config_data or {})
                cfg["revoked"] = {"at": now.isoformat(), "reason": reason[:200], "trace": trace_id}
                await self.repo.save_subscription(replace(
                    row, active=False, provisioning_state="expired", valid_until=new_expire, config_data=cfg,
                ))
            await self.obhod.on_main_revoked(tg, trace_id)
            await self._invalidate(tg)
            logger.info(f"[{trace_id}] provisioning: revoked tg={tg} reason={reason[:80]!r}")
            return True
        finally:
            await self._unlock(lock)



class CreditsMixin:
    async def _credit_once(self, trace_id: str) -> bool:
        from app.infra.redis.flags import set_once

        got = await set_once(f"credit:{trace_id}", "1", ttl=CREDIT_MARKER_TTL_S)
        if got is False:
            logger.info(f"[{trace_id}] provisioning: credit already applied")
            return False
        return True

    async def add_days(self, telegram_id: int, days: int, *, trace_id: str, reason: str = "") -> Optional[datetime]:
        """Extend an EXISTING main account by ``days`` from max(now, expiry).
        Returns the new expiry, the unchanged one for lifetime, None when there
        is no account / not applied. Squads and limits are not touched."""
        if int(days) <= 0:
            raise ValueError("days must be positive")
        tg = int(telegram_id)
        user = await self.accounts.find_main(tg)
        if user is None:
            return None
        if user.expire_at and user.expire_at.year >= 2099:
            return user.expire_at
        if (user.status or "").upper() == "DISABLED":
            return None
        if not await self._credit_once(trace_id):
            return user.expire_at
        now = self.clock()
        current = user.expire_at if (user.expire_at and user.expire_at.year >= 2020) else None
        target = compute_target(Entitlement(plan_code="-", source=EntitlementSource.ADMIN, days=int(days)),
                                current, now)
        updated = await self.remna.update_user(user.id, expire_at=target, current=user)
        row = await self.repo.get_subscription(tg, SubKind.MAIN)
        if row is not None and row.active:
            await self.repo.save_subscription(replace(row, valid_until=updated.expire_at or target,
                                                      remnawave_expected_expire_at=updated.expire_at or target))
        await self._invalidate(tg)
        logger.info(f"[{trace_id}] provisioning: +{days}d tg={tg} reason={reason[:80]!r}")
        return updated.expire_at or target

    async def add_traffic(self, telegram_id: int, extra_bytes: int, *, trace_id: str,
                          sub_kind: SubKind = SubKind.OBHOD) -> Optional[int]:
        """Raise the traffic cap of the obhod (default) or main account by
        ``extra_bytes``. 0 (unlimited) stays unlimited. Returns the new cap."""
        if int(extra_bytes) <= 0:
            raise ValueError("extra_bytes must be positive")
        tg = int(telegram_id)
        if SubKind(sub_kind) is SubKind.OBHOD:
            row = await self.repo.get_subscription(tg, SubKind.OBHOD)
            pid = numeric_panel_id(row.remna_user_id) if row and row.active else None
            user = await self.remna.get_user(pid) if pid else None
        else:
            user = await self.accounts.find_main(tg)
        if user is None:
            return None
        cur = int(user.traffic_limit_bytes or 0)
        if cur == 0:
            return 0
        if not await self._credit_once(trace_id):
            return cur
        new = cur + int(extra_bytes)
        await self.remna.update_user(user.id, traffic_limit_bytes=new, current=user)
        await self._invalidate(tg)
        return new

    async def add_devices(self, telegram_id: int, extra: int, *, trace_id: str) -> Optional[int]:
        """Raise the HWID limit of the main account by ``extra``. 0 (unlimited)
        and NULL (panel fallback) are left alone. Returns the new limit."""
        if int(extra) <= 0:
            raise ValueError("extra must be positive")
        tg = int(telegram_id)
        user = await self.accounts.find_main(tg)
        if user is None or user.device_limit in (None, 0):
            return user.device_limit if user else None
        if not await self._credit_once(trace_id):
            return user.device_limit
        new = int(user.device_limit) + int(extra)
        await self.remna.update_user(user.id, device_limit=new, current=user)
        await self._invalidate(tg)
        return new
