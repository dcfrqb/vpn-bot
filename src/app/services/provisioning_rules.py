"""Pure rules and small types of ProvisioningService (stream B).

Split out of services/provisioning.py to keep modules small. Everything here
is re-exported from app.services.provisioning.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol, Sequence

from dateutil.relativedelta import relativedelta

from app.domain.models import Entitlement
from app.domain.plans import is_obhod_eligible_plan
from app.logger import logger
from app.services.remna_tariff import is_manual_squad_name, managed_tariff_squad_names

LIFETIME = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
GRACE_STATES = ("active", "ended")  # subscriptions.grace_state written by stream C
VERIFY_TOLERANCE = timedelta(minutes=5)
REVOKE_GRACE = timedelta(minutes=5)
MAX_GRANT_RECORDS = 30
LATE_PATCH_DELAY_S = 3.0  # review N6: a timed-out PATCH may land a bit later
DISABLED_ALERT_TTL_S = 6 * 3600
CREDIT_MARKER_TTL_S = 90 * 24 * 3600


class ProvisioningError(Exception):
    """Access was not applied (panel/DB failure). Retryable."""


class ProvisioningBusy(ProvisioningError):
    """Another grant for this user holds the lock longer than LOCK_WAIT_S."""


class GrantRefused(Exception):
    """Expected refusal: nothing was written. ``reason``: disabled | bad_plan."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


class ObhodSync(Protocol):
    """How the obhod account follows the main one (2.x obhod_service in prod)."""

    async def on_main_granted(self, telegram_id: int, plan_code: str, valid_until: datetime, trace_id: str) -> None: ...

    async def on_main_revoked(self, telegram_id: int, trace_id: str) -> None: ...


class LegacyObhodSync:
    """ObhodSync over services.obhod_service (one DB session per call).
    Soft-fail: an obhod problem never breaks the main grant."""

    async def _session(self):
        from app.db import session as db_session

        if db_session.SessionLocal is None:
            return None
        return db_session.SessionLocal()

    async def on_main_granted(self, telegram_id: int, plan_code: str, valid_until: datetime, trace_id: str) -> None:
        from app.services import obhod_service

        factory = await self._session()
        if factory is None:
            return
        naive = valid_until.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            async with factory as session:
                if is_obhod_eligible_plan(plan_code):
                    await obhod_service.ensure_obhod_for_pro(
                        session=session, telegram_user_id=int(telegram_id), plan_code=plan_code,
                        valid_until=naive, trace_id=trace_id,
                    )
                else:
                    await obhod_service.deactivate_obhod(session, int(telegram_id), trace_id=trace_id)
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{trace_id}] obhod sync soft-fail tg={telegram_id} ({type(e).__name__})")

    async def on_main_revoked(self, telegram_id: int, trace_id: str) -> None:
        from app.services import obhod_service

        factory = await self._session()
        if factory is None:
            return
        try:
            async with factory as session:
                await obhod_service.deactivate_obhod(session, int(telegram_id), trace_id=trace_id)
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{trace_id}] obhod revoke soft-fail tg={telegram_id} ({type(e).__name__})")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def grant_key(entitlement: Entitlement, trace_id: str) -> str:
    return f"pay:{entitlement.payment_id}" if entitlement.payment_id else f"trace:{trace_id}"


def compute_target(entitlement: Entitlement, current: Optional[datetime], now: datetime,
                   months: Optional[int] = None) -> datetime:
    """New expiry from max(now, current): ``months`` (calendar months, as 2.x
    payments) or ``days``; or an exact ``until``; or lifetime. Never earlier
    than ``current`` (the panel date is not shortened)."""
    if entitlement.is_lifetime or (current is not None and current.year >= LIFETIME.year):
        return max(LIFETIME, current) if current is not None else LIFETIME
    base = current if (current is not None and current > now) else now
    if months:
        target = base + relativedelta(months=int(months))
    elif entitlement.until is not None:
        until = entitlement.until if entitlement.until.tzinfo else entitlement.until.replace(tzinfo=timezone.utc)
        target = until.astimezone(timezone.utc)
    elif entitlement.days is not None:
        target = base + timedelta(days=int(entitlement.days))
    else:
        raise GrantRefused("bad_entitlement", "entitlement needs months, days, until or is_lifetime")
    if current is not None and current > target:
        return current  # never shorten (a manual extension is already ahead)
    return min(target, LIFETIME)


def target_squads(current_names: Sequence[str], plan_squad: str, *, grace_squad: Optional[str] = None,
                  clear_grace: bool = True) -> list[str]:
    """Full target list of NON-manual squad names for RemnaGateway.update_user:
    drop the bot's tariff squads (and the grace squad), add the plan squad,
    keep the rest (us-2, esp, full, obhod, ...). Manual squads are kept by the
    gateway itself and never appear here."""
    managed = managed_tariff_squad_names()
    out: list[str] = []
    for n in current_names:
        if is_manual_squad_name(n) or n in managed:
            continue
        if clear_grace and grace_squad and n == grace_squad:
            continue
        if n not in out:
            out.append(n)
    if plan_squad not in out:
        out.append(plan_squad)
    return out
