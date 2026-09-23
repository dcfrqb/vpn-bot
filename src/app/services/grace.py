"""Grace period («льготный» squad) after a subscription expires. Owner: C.

Behind GRACE_ENABLED (default off) and GRACE_SQUAD. No aiogram here: the
caller (worker.panel_events, worker.jobs.grace) sends the notices.

Start (on the panel user.expired webhook):
  - only for users who paid at least once, were not refunded, are not
    lifetime, have no manual squad (*-m, *-friend, arcadia) and did not
    have a grace for this expiry yet (grace_state is empty);
  - DB first: subscriptions.grace_until = now + GRACE_DAYS, grace_state =
    'active' (grace_until is the source of truth; valid_until is never
    touched, grace is not paid time);
  - panel: squads = [GRACE_SQUAD], traffic GRACE_DAILY_GB per DAY,
    expireAt = grace_until, device limit unchanged; enable if the panel
    left the user EXPIRED. On a panel error the DB mark is rolled back.
End (grace job): the panel itself expires the user at expireAt; the job
marks grace_state = 'ended' and disables the user only if the panel still
shows ACTIVE. A payment in between goes through ProvisioningService.grant,
which (stream B) extends from the PAID term, restores the plan squad and
traffic limit and calls ``GraceService.clear``.

Grace is refused unless ProvisioningService.grant accepts ``clear_grace``
(stream B's PanelProvisioningService): a grant without it keeps unknown
squads and the traffic cap, so a paying user would stay on the grace squad
with 5 GB a day.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.domain.models import PanelUser, ensure_utc
from app.logger import logger
from app.services.events_repo import GRACE_ACTIVE, GRACE_ENDED, EventsRepo

GIB = 1024 ** 3
PAID_MARGIN = timedelta(hours=1)


def _supports_clear_grace(provisioning: Any) -> bool:
    """True when ``provisioning.grant`` takes ``clear_grace`` (stream B's service)."""
    import inspect

    grant = getattr(provisioning, "grant", None)
    if grant is None:
        return False
    try:
        params = inspect.signature(grant).parameters
    except (TypeError, ValueError):
        return False
    return "clear_grace" in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


@dataclass(frozen=True)
class GraceDecision:
    ok: bool
    reason: str = ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GraceService:
    def __init__(
        self,
        remna: Any,
        repo: EventsRepo,
        *,
        provisioning: Any = None,
        settings: Any = None,
        clock: Callable[[], datetime] = _utcnow,
    ):
        self.remna = remna
        self.repo = repo
        self.provisioning = provisioning
        self._settings = settings
        self.clock = clock

    @property
    def settings(self):
        if self._settings is not None:
            return self._settings
        from app.config import settings

        return settings

    @property
    def squad(self) -> Optional[str]:
        name = (getattr(self.settings, "GRACE_SQUAD", None) or "").strip()
        return name or None

    def configured(self) -> GraceDecision:
        from app.services.remna_tariff import is_manual_squad_name

        if not getattr(self.settings, "GRACE_ENABLED", False):
            return GraceDecision(False, "disabled")
        if not self.squad:
            return GraceDecision(False, "no_squad")
        if is_manual_squad_name(self.squad):
            return GraceDecision(False, "squad_is_manual")
        if not _supports_clear_grace(self.provisioning):
            return GraceDecision(False, "provisioning_not_ready")
        return GraceDecision(True)

    async def eligible(self, telegram_id: int, user: PanelUser) -> GraceDecision:
        from app.services.remna_tariff import is_manual_squad_name

        cfg = self.configured()
        if not cfg.ok:
            return cfg
        if user.telegram_id is not None and int(user.telegram_id) != int(telegram_id):
            return GraceDecision(False, "foreign_user")
        info = await self.repo.reminder_info(telegram_id)
        if info.is_lifetime:
            return GraceDecision(False, "lifetime")
        if info.refunded:
            return GraceDecision(False, "refunded")
        if not info.has_paid:
            return GraceDecision(False, "never_paid")
        if info.grace_state:
            return GraceDecision(False, f"already_{info.grace_state}")
        # Webhook payloads may carry empty squads: read the live user.
        fresh = await self.remna.get_user(user.id)
        if fresh is None:
            return GraceDecision(False, "panel_user_missing")
        if any(is_manual_squad_name(s) for s in fresh.squads):
            return GraceDecision(False, "manual_squad")
        return GraceDecision(True)

    async def start(self, telegram_id: int, user: PanelUser) -> Optional[datetime]:
        """Put the user into grace. Returns grace_until, or None when not eligible."""
        decision = await self.eligible(telegram_id, user)
        if not decision.ok:
            logger.info(f"grace: tg={telegram_id} skipped ({decision.reason})")
            return None
        days = int(getattr(self.settings, "GRACE_DAYS", 3) or 3)
        daily_gb = int(getattr(self.settings, "GRACE_DAILY_GB", 5) or 5)
        until = (self.clock() + timedelta(days=days)).replace(microsecond=0)
        if not await self.repo.set_grace(telegram_id, until=until, state=GRACE_ACTIVE):
            logger.warning(f"grace: tg={telegram_id} has no main row, skipped")
            return None
        try:
            updated = await self.remna.update_user(
                user.id,
                expire_at=until,
                squads=[self.squad],
                traffic_limit_bytes=daily_gb * GIB,
                traffic_limit_strategy="DAY",
            )
            if (updated.status or "").upper() != "ACTIVE":
                await self.remna.enable_user(user.id)
        except Exception as e:  # noqa: BLE001 - roll back the DB mark, caller logs
            await self.repo.set_grace(telegram_id, until=None, state=None)
            logger.warning(f"grace: tg={telegram_id} panel update failed ({type(e).__name__}), rolled back")
            raise
        logger.info(f"grace: tg={telegram_id} panel_id={user.id} until={until.isoformat()}")
        return until

    async def end_due(self) -> list[int]:
        """Close every grace whose grace_until has passed. Returns telegram ids
        whose access actually ended (the caller notifies them)."""
        now = self.clock()
        ended: list[int] = []
        for row in await self.repo.due_graces(now):
            try:
                if await self._end_one(row, now):
                    ended.append(row.telegram_id)
            except Exception as e:  # noqa: BLE001 - one user must not stop the rest
                logger.warning(f"grace: end tg={row.telegram_id} failed ({type(e).__name__})")
        return ended

    async def _end_one(self, row, now: datetime) -> bool:
        panel_id = int(row.remna_user_id) if row.remna_user_id and str(row.remna_user_id).isdigit() else None
        user = await self.remna.get_user(panel_id) if panel_id else None
        if user is None:
            found = await self.remna.find_users_by_telegram_id(row.telegram_id)
            user = found[0] if found else None
        if user is not None and user.expire_at is not None and row.grace_until is not None:
            if ensure_utc(user.expire_at) > row.grace_until + PAID_MARGIN:
                # Paid in the meantime and provisioning did not clear the mark.
                await self.clear(row.telegram_id)
                logger.info(f"grace: tg={row.telegram_id} paid during grace, mark cleared")
                return False
        if user is not None and (user.status or "").upper() == "ACTIVE":
            await self.remna.disable_user(user.id)
        await self.repo.set_grace(row.telegram_id, until=row.grace_until, state=GRACE_ENDED)
        logger.info(f"grace: tg={row.telegram_id} ended")
        return True

    async def clear(self, telegram_id: int) -> None:
        """Forget the grace mark (called by provisioning after a payment)."""
        await self.repo.set_grace(telegram_id, until=None, state=None)


def in_grace(grace_until: Optional[datetime], grace_state: Optional[str], now: Optional[datetime] = None) -> bool:
    """Pure helper for status screens: is this user inside an active grace?"""
    if grace_state != GRACE_ACTIVE or grace_until is None:
        return False
    return ensure_utc(grace_until) > (now or _utcnow())
