"""Mixins of PanelProvisioningService (stream B), split out to keep modules small.

RevokeMixin.rollback / revoke: take a refunded period back (exactly the paid
months, or a full cut: expireAt = now + 5 min, row inactive, obhod off) under
the per-user provisioning lock; lifetime and manual-squad users are never cut.
CreditsMixin: add_days / add_traffic / add_devices for EXISTING panel
accounts. Never create an account, never lower anything, idempotent per
trace_id (Redis marker).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional

from dateutil.relativedelta import relativedelta

from app.domain.models import Entitlement, EntitlementSource, SubKind
from app.logger import logger
from app.services.accounts import numeric_panel_id
from app.domain.plans import is_obhod_eligible_plan
from app.services.provisioning_rules import (
    CREDIT_MARKER_TTL_S,
    MAX_GRANT_RECORDS,
    REVOKE_GRACE,
    VERIFY_TOLERANCE,
    compute_target,
)
from app.services.remna_tariff import is_manual_squad_name


@dataclass(frozen=True)
class RollbackResult:
    """Outcome of ``rollback``: ``expired`` (full cut, expireAt = now + 5 min),
    ``shortened`` (the refunded months taken off, access stays), ``skipped``
    (lifetime or manual squad: admin alerted), ``no_account``."""

    action: str
    expire_at: Optional[datetime] = None

    @property
    def changed(self) -> bool:
        return self.action in ("expired", "shortened")


def _iso_or_none(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class RevokeMixin:

    async def revoke(self, telegram_id: int, *, sub_kind: SubKind = SubKind.MAIN, reason: str,
                     trace_id: str, months: Optional[int] = None) -> bool:
        """Full cut (``months`` None) or roll back exactly ``months`` paid months
        (review money M-1). True when access changed."""
        tg = int(telegram_id)
        if SubKind(sub_kind) is SubKind.OBHOD:
            await self.obhod.on_main_revoked(tg, trace_id)
            await self._invalidate(tg)
            return True
        res = await self.rollback(tg, months=months, reason=reason, trace_id=trace_id)
        return res.changed

    async def rollback(self, telegram_id: int, *, months: Optional[int], reason: str,
                       trace_id: str) -> RollbackResult:
        """Take a refunded period back, under the same per-user lock as grant.

        ``months`` > 0: new expiry = current panel expiry - months; if that is
        not in the future (+5 min), a full cut. ``months`` None/0: full cut.
        Full cut = expireAt now + 5 min (the panel refuses past dates and turns
        the user EXPIRED itself), row inactive, obhod off. A shortened Pro term
        is copied to obhod. Lifetime and manual-squad users are never touched
        (admin alert). Idempotent per ``trace_id``: the result is recorded in
        config_data.rollbacks; a retry re-applies the recorded date only while
        the panel still sits on the recorded base (else it recomputes, so a
        grant that landed in between is not erased)."""
        tg = int(telegram_id)
        lock = await self._lock(tg)
        try:
            return await self._rollback_locked(tg, months=int(months or 0), reason=reason, trace_id=trace_id)
        finally:
            await self._unlock(lock)

    async def _rollback_locked(self, tg: int, *, months: int, reason: str, trace_id: str) -> RollbackResult:
        user = await self.accounts.find_main(tg)
        row = await self.repo.get_subscription(tg, SubKind.MAIN)
        if user is None:
            return RollbackResult("no_account")
        rec = (((row.config_data if row else {}) or {}).get("rollbacks") or {}).get(trace_id)
        if rec and rec.get("state") == "applied":
            logger.info(f"[{trace_id}] provisioning: rollback already applied tg={tg}")
            return RollbackResult(rec.get("action", "expired"), _parse_dt(rec.get("target")))
        names = await self._squad_names(user)
        manual = [n for n in names if is_manual_squad_name(n)]
        is_lifetime = bool(user.expire_at and user.expire_at.year >= 2099)
        if is_lifetime or manual:
            await self._alert(
                "Отзыв доступа пропущен: подписка навсегда или ручной сквад.\n"
                f"Telegram ID: {tg}\nПричина: {reason}\nРешите вручную в панели.",
                dedup_key=f"revoke_skipped:{tg}:{trace_id}",
            )
            return RollbackResult("skipped", user.expire_at)

        now = self.clock()
        current = user.expire_at if (user.expire_at and user.expire_at.year >= 2020) else None
        cut = now + REVOKE_GRACE
        target: Optional[datetime] = None
        if rec and rec.get("state") == "pending" and rec.get("action") == "shortened":
            base, recorded = _parse_dt(rec.get("base")), _parse_dt(rec.get("target"))
            if current is not None and recorded is not None and base is not None and (
                    abs(current - base) <= VERIFY_TOLERANCE or abs(current - recorded) <= VERIFY_TOLERANCE):
                target = recorded
        if target is None and months > 0 and current is not None:
            target = current - relativedelta(months=months)
        if target is None or target <= cut:
            action, target = "expired", cut
        else:
            action = "shortened"

        if row is not None:  # Phase A: the intent before the panel write
            cfg = dict(row.config_data or {})
            rbs = dict(cfg.get("rollbacks") or {})
            rbs[trace_id] = {"state": "pending", "action": action, "target": target.isoformat(),
                             "base": _iso_or_none(current), "months": months, "at": now.isoformat()}
            if len(rbs) > MAX_GRANT_RECORDS:
                for old in sorted(rbs, key=lambda k: rbs[k].get("at", ""))[: len(rbs) - MAX_GRANT_RECORDS]:
                    rbs.pop(old, None)
            cfg["rollbacks"] = rbs
            row = await self.repo.save_subscription(replace(row, config_data=cfg))

        if action == "shortened" or current is None or current > target:
            await self.remna.update_user(user.id, expire_at=target, current=user)

        if row is not None:
            cfg = dict(row.config_data or {})
            rbs = dict(cfg.get("rollbacks") or {})
            rbs[trace_id] = {**rbs.get(trace_id, {}), "state": "applied"}
            cfg["rollbacks"] = rbs
            if action == "expired":
                cfg["revoked"] = {"at": now.isoformat(), "reason": reason[:200], "trace": trace_id}
                row = replace(row, active=False, provisioning_state="expired", valid_until=target,
                              remnawave_expected_expire_at=target, config_data=cfg)
            else:
                row = replace(row, valid_until=target, remnawave_expected_expire_at=target, config_data=cfg)
            row = await self.repo.save_subscription(row)

        if action == "expired":
            await self.obhod.on_main_revoked(tg, trace_id)
        elif row is not None and is_obhod_eligible_plan(row.plan_code):
            # A shortened Pro term: obhod gets the same date (2.x refund m2).
            await self.obhod.on_main_granted(tg, row.plan_code, target, trace_id)
        await self._invalidate(tg)
        logger.info(f"[{trace_id}] provisioning: rollback tg={tg} action={action} months={months} "
                    f"expire={target.isoformat()} reason={reason[:80]!r}")
        return RollbackResult(action, target)


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
        Returns the new expiry; None when there is no account, a lifetime one
        or a DISABLED one (nothing credited). Runs under the per-user
        provisioning lock; a failed PATCH releases the idempotency marker. Squads and limits are not touched."""
        if int(days) <= 0:
            raise ValueError("days must be positive")
        tg = int(telegram_id)
        lock = await self._lock(tg)  # same per-user lock as grant/revoke
        try:
            return await self._add_days_locked(tg, int(days), trace_id=trace_id, reason=reason)
        finally:
            await self._unlock(lock)

    async def _add_days_locked(self, tg: int, days: int, *, trace_id: str, reason: str) -> Optional[datetime]:
        user = await self.accounts.find_main(tg)
        if user is None:
            return None
        if user.expire_at and user.expire_at.year >= 2099:
            return None  # lifetime: nothing to add (callers count it as skipped)
        if (user.status or "").upper() == "DISABLED":
            return None
        if not await self._credit_once(trace_id):
            return user.expire_at
        now = self.clock()
        current = user.expire_at if (user.expire_at and user.expire_at.year >= 2020) else None
        target = compute_target(Entitlement(plan_code="-", source=EntitlementSource.ADMIN, days=int(days)),
                                current, now)
        try:
            updated = await self.remna.update_user(user.id, expire_at=target, current=user)
        except Exception:
            # Not applied: drop the marker so a retry of the same trace_id credits.
            from app.infra.redis.flags import delete_key

            await delete_key(f"credit:{trace_id}")
            raise
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
