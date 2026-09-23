"""DB access for stream C (panel events, reminders, grace). No aiogram.

One small repository so the event handlers, the reminders job and the grace
service never write SQL themselves. Tests use tests/events/fakes.FakeEventsRepo;
tests/integration/test_events_repo_real_postgres.py runs this class on Postgres.

Rules kept here:
- every subscriptions query filters on sub_kind (main vs obhod);
- grace_until/grace_state are the source of truth for the grace period;
  valid_until is NEVER moved by grace (grace is not paid time);
- a payment with provider 'promo' is not a purchase.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

from app.domain.models import ensure_utc
from app.logger import logger

GRACE_ACTIVE = "active"
GRACE_ENDED = "ended"

EXPIRE_MARKED = "marked"
EXPIRE_ALREADY = "already"
EXPIRE_PAID_LATER = "paid_later"
EXPIRE_NO_ROW = "no_row"

# payments.kind values that are not a subscription purchase
_NON_SUB_KINDS = ("obhod_package", "gift")


@dataclass(frozen=True)
class ReminderInfo:
    """What the reminders job needs to know about one user."""

    telegram_id: int
    autorenew: bool = False
    refunded: bool = False
    is_lifetime: bool = False
    last_plan_code: Optional[str] = None
    last_months: Optional[int] = None
    grace_state: Optional[str] = None
    has_paid: bool = False


@dataclass(frozen=True)
class GraceRow:
    telegram_id: int
    grace_until: Optional[datetime]
    grace_state: Optional[str]
    remna_user_id: Optional[str] = None


class EventsRepo(Protocol):
    async def reminder_info(self, telegram_id: int) -> ReminderInfo: ...

    async def mark_main_expired(self, telegram_id: int, now: datetime) -> str: ...

    async def obhod_panel_id(self, telegram_id: int) -> Optional[str]: ...

    async def deactivate_obhod_row(self, telegram_id: int) -> bool: ...

    async def obhod_owner(self, panel_id: int, uuid: str = "") -> Optional[int]: ...

    async def get_grace(self, telegram_id: int) -> Optional[GraceRow]: ...

    async def set_grace(self, telegram_id: int, *, until: Optional[datetime], state: Optional[str]) -> bool: ...

    async def due_graces(self, now: datetime) -> list[GraceRow]: ...


def _plan_from_payment(p: Any) -> tuple[Optional[str], Optional[int]]:
    meta = p.payment_metadata if isinstance(p.payment_metadata, dict) else {}
    code = p.plan_code or meta.get("plan_code")
    months = p.period_months or meta.get("period_months")
    try:
        months = int(months) if months else None
    except (TypeError, ValueError):
        months = None
    return (str(code) if code else None), months


def _is_refunded(p: Any) -> bool:
    if p.status == "refunded":
        return True
    meta = p.payment_metadata if isinstance(p.payment_metadata, dict) else {}
    try:
        return float(p.refunded_amount or meta.get("refunded_amount") or 0) > 0
    except (TypeError, ValueError):
        return False


def _naive_utc(dt: datetime) -> datetime:
    """DB columns are naive UTC."""
    return ensure_utc(dt).replace(tzinfo=None)


class SqlEventsRepo:
    """EventsRepo over app.db (SessionLocal). ``session_factory`` for tests."""

    def __init__(self, session_factory: Any = None):
        self._factory = session_factory

    def _session(self):
        if self._factory is not None:
            return self._factory()
        from app.db.session import SessionLocal

        if SessionLocal is None:
            raise RuntimeError("DATABASE_URL is not set")
        return SessionLocal()

    async def _main_sub(self, session, telegram_id: int):
        from sqlalchemy import desc, select

        from app.db.models import Subscription

        stmt = (
            select(Subscription)
            .where(Subscription.telegram_user_id == int(telegram_id), Subscription.sub_kind == "main")
            .order_by(desc(Subscription.active), desc(Subscription.valid_until).nulls_last(), desc(Subscription.id))
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def reminder_info(self, telegram_id: int) -> ReminderInfo:
        from sqlalchemy import desc, func, select

        from app.db.models import Payment

        async with self._session() as session:
            sub = await self._main_sub(session, telegram_id)
            stmt = (
                select(Payment)
                .where(
                    Payment.telegram_user_id == int(telegram_id),
                    Payment.provider != "promo",
                    Payment.status.in_(("succeeded", "refunded")),
                )
                .order_by(desc(func.coalesce(Payment.paid_at, Payment.created_at)), desc(Payment.id))
                .limit(20)
            )
            payments = list((await session.execute(stmt)).scalars())
        subs = [p for p in payments if (p.kind or "subscription") not in _NON_SUB_KINDS]
        plan_code, months = None, None
        for p in subs:
            code, m = _plan_from_payment(p)
            if code and not code.startswith("obhod"):
                plan_code, months = code, m
                break
        if plan_code is None and sub is not None and sub.plan_code and sub.plan_code != "trial":
            plan_code = sub.plan_code
        return ReminderInfo(
            telegram_id=int(telegram_id),
            autorenew=bool(sub is not None and sub.autorenew),
            refunded=bool(subs and _is_refunded(subs[0])),
            is_lifetime=bool(sub is not None and sub.is_lifetime),
            last_plan_code=plan_code,
            last_months=months,
            grace_state=(sub.grace_state if sub is not None else None),
            has_paid=any(p.status == "succeeded" for p in subs),
        )

    async def mark_main_expired(self, telegram_id: int, now: datetime) -> str:
        """active=False on the main row, only when its own term is over (a
        payment that landed a moment before the webhook is not undone).

        Returns EXPIRE_MARKED, EXPIRE_ALREADY (row already inactive),
        EXPIRE_PAID_LATER (lifetime or a later DB term: do nothing else) or
        EXPIRE_NO_ROW.
        """
        async with self._session() as session:
            sub = await self._main_sub(session, telegram_id)
            if sub is None:
                return EXPIRE_NO_ROW
            if sub.is_lifetime:
                return EXPIRE_PAID_LATER
            if sub.valid_until is not None and sub.valid_until > _naive_utc(now) + timedelta(minutes=5):
                logger.info(f"events: expired webhook for tg={telegram_id}, but DB term is later; row kept")
                return EXPIRE_PAID_LATER
            if not sub.active:
                return EXPIRE_ALREADY
            sub.active = False
            await session.commit()
            return EXPIRE_MARKED

    async def _obhod_sub(self, session, telegram_id: int):
        from sqlalchemy import desc, select

        from app.db.models import Subscription

        stmt = (
            select(Subscription)
            .where(Subscription.telegram_user_id == int(telegram_id), Subscription.sub_kind == "obhod")
            .order_by(desc(Subscription.id))
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def obhod_panel_id(self, telegram_id: int) -> Optional[str]:
        async with self._session() as session:
            sub = await self._obhod_sub(session, telegram_id)
            if sub is None or not sub.active:
                return None
            return sub.remna_user_id

    async def deactivate_obhod_row(self, telegram_id: int) -> bool:
        async with self._session() as session:
            sub = await self._obhod_sub(session, telegram_id)
            if sub is None or not sub.active:
                return False
            sub.active = False
            await session.commit()
            return True

    async def obhod_owner(self, panel_id: int, uuid: str = "") -> Optional[int]:
        from sqlalchemy import select

        from app.db.models import Subscription

        keys = [str(int(panel_id))] + ([uuid] if uuid else [])
        async with self._session() as session:
            stmt = (
                select(Subscription.telegram_user_id)
                .where(Subscription.sub_kind == "obhod", Subscription.remna_user_id.in_(keys))
                .limit(1)
            )
            row = (await session.execute(stmt)).first()
        return int(row[0]) if row else None

    async def get_grace(self, telegram_id: int) -> Optional[GraceRow]:
        async with self._session() as session:
            sub = await self._main_sub(session, telegram_id)
            if sub is None:
                return None
            return GraceRow(int(telegram_id), ensure_utc(sub.grace_until), sub.grace_state, sub.remna_user_id)

    async def set_grace(self, telegram_id: int, *, until: Optional[datetime], state: Optional[str]) -> bool:
        async with self._session() as session:
            sub = await self._main_sub(session, telegram_id)
            if sub is None:
                return False
            sub.grace_until = _naive_utc(until) if until is not None else None
            sub.grace_state = state
            await session.commit()
            return True

    async def due_graces(self, now: datetime) -> list[GraceRow]:
        from sqlalchemy import select

        from app.db.models import Subscription

        async with self._session() as session:
            stmt = select(Subscription).where(
                Subscription.sub_kind == "main",
                Subscription.grace_state == GRACE_ACTIVE,
                Subscription.grace_until <= _naive_utc(now),
            )
            rows = list((await session.execute(stmt)).scalars())
        return [GraceRow(r.telegram_user_id, ensure_utc(r.grace_until), r.grace_state, r.remna_user_id) for r in rows]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
