"""Broadcast segments (stream E): who gets a broadcast.

all | active | expired | never | trial_nc | ids, with sub_kind (default
"main") and "within N days" for active / expired / trial_nc. Every condition
on subscriptions carries the sub_kind filter (invariant 4). Customer money
only (NON_REVENUE_PROVIDERS excluded) for never / trial_nc.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Union

SEGMENT_ALL = "all"
SEGMENT_ACTIVE = "active"
SEGMENT_EXPIRED = "expired"
SEGMENT_NEVER = "never"
SEGMENT_TRIAL_NC = "trial_nc"  # took a trial, never paid
SEGMENT_IDS = "ids"  # explicit Telegram ids (ignores opt-out: the admin chose them)
VALID_SEGMENTS = {SEGMENT_ALL, SEGMENT_ACTIVE, SEGMENT_EXPIRED, SEGMENT_NEVER}  # 2.x wizard
SEGMENTS = (SEGMENT_ALL, SEGMENT_ACTIVE, SEGMENT_EXPIRED, SEGMENT_NEVER, SEGMENT_TRIAL_NC, SEGMENT_IDS)
SUB_KINDS = ("main", "obhod")
MAX_IDS = 1000


# =============================================================================
# Segments
# =============================================================================


@dataclass(frozen=True)
class Segment:
    kind: str = SEGMENT_ALL
    sub_kind: str = "main"
    days: Optional[int] = None
    ids: tuple[int, ...] = ()

    def __post_init__(self):
        if self.kind not in SEGMENTS:
            raise ValueError(f"unknown segment: {self.kind!r}")
        if self.sub_kind not in SUB_KINDS:
            raise ValueError(f"unknown sub_kind: {self.sub_kind!r}")
        if self.days is not None and int(self.days) <= 0:
            raise ValueError("days must be positive")
        if self.kind == SEGMENT_IDS and not self.ids:
            raise ValueError("ids segment needs ids")
        if len(self.ids) > MAX_IDS:
            raise ValueError("too many ids")

    @classmethod
    def from_row(cls, segment: str, params: Optional[dict]) -> "Segment":
        p = dict(params or {})
        return cls(kind=p.get("kind") or segment, sub_kind=p.get("sub_kind") or "main",
                   days=p.get("days"), ids=tuple(int(i) for i in (p.get("ids") or ())))

    def to_params(self) -> dict:
        out: dict[str, Any] = {"kind": self.kind, "sub_kind": self.sub_kind}
        if self.days:
            out["days"] = int(self.days)
        if self.ids:
            out["ids"] = list(self.ids)
        return out

    @property
    def code(self) -> str:
        """broadcasts.segment (varchar 16)."""
        return self.kind


def _real_payment_exists(tg_col: Any) -> Any:
    from sqlalchemy import and_, exists

    from app.db.models import Payment
    from app.services.promo import NON_REVENUE_PROVIDERS

    return exists().where(and_(
        Payment.telegram_user_id == tg_col, Payment.status == "succeeded",
        Payment.provider.notin_(NON_REVENUE_PROVIDERS)))


def segment_filter(seg: Segment, now: Optional[datetime] = None) -> Any:
    """SQL condition on telegram_users for a segment. Subscription conditions
    always carry the sub_kind filter (invariant 4)."""
    from sqlalchemy import String, and_, cast, exists, func, or_

    from app.db.models import Payment, Subscription, TelegramUser, Trial

    now = now or datetime.utcnow()
    if seg.kind == SEGMENT_IDS:
        return TelegramUser.telegram_id.in_(list(seg.ids))
    base = and_(TelegramUser.is_active.is_(True), TelegramUser.broadcast_opt_out.is_(False))
    own = and_(Subscription.telegram_user_id == TelegramUser.telegram_id, Subscription.sub_kind == seg.sub_kind)
    active_cond = and_(own, Subscription.active.is_(True),
                       or_(Subscription.is_lifetime.is_(True), Subscription.valid_until > now))
    if seg.kind == SEGMENT_ALL:
        return base
    if seg.kind == SEGMENT_ACTIVE:
        if seg.days:
            soon = and_(own, Subscription.active.is_(True), Subscription.is_lifetime.isnot(True),
                        Subscription.valid_until > now,
                        Subscription.valid_until <= now + timedelta(days=int(seg.days)))
            return and_(base, exists().where(soon))
        return and_(base, exists().where(active_cond))
    if seg.kind == SEGMENT_EXPIRED:
        had = and_(own, Subscription.valid_until.isnot(None))
        if seg.days:
            had = and_(had, Subscription.valid_until > now - timedelta(days=int(seg.days)),
                       Subscription.valid_until <= now)
        return and_(base, exists().where(had), ~exists().where(active_cond))
    if seg.kind == SEGMENT_NEVER:
        return and_(base, ~_real_payment_exists(TelegramUser.telegram_id))
    if seg.kind == SEGMENT_TRIAL_NC:
        trial = Trial.telegram_user_id == TelegramUser.telegram_id
        promo_trial = and_(Payment.provider == "promo",
                           Payment.external_id == func.concat("promo_trial_", cast(TelegramUser.telegram_id, String)))
        if seg.days:
            cutoff = now - timedelta(days=int(seg.days))
            trial = and_(trial, Trial.started_at >= cutoff)
            promo_trial = and_(promo_trial, Payment.paid_at >= cutoff)
        return and_(base, or_(exists().where(trial), exists().where(promo_trial)),
                    ~_real_payment_exists(TelegramUser.telegram_id))
    raise ValueError(f"unknown segment: {seg.kind!r}")


def _segment_filter(segment: str) -> Any:
    """2.x name: filter for a plain segment code."""
    if segment not in SEGMENTS or segment == SEGMENT_IDS:
        raise ValueError(f"unknown segment: {segment!r}")
    return segment_filter(Segment(kind=segment))


def _as_segment(segment: Union[str, Segment]) -> Segment:
    return segment if isinstance(segment, Segment) else Segment(kind=segment)


async def count_segment(segment: Union[str, Segment]) -> int:
    from sqlalchemy import func, select

    from app.db.models import TelegramUser
    from app.db.session import SessionLocal

    seg = _as_segment(segment)
    if not SessionLocal:
        return 0
    async with SessionLocal() as session:
        stmt = select(func.count()).select_from(TelegramUser).where(segment_filter(seg))
        return int((await session.execute(stmt)).scalar_one())


async def _segment_user_ids(seg: Segment) -> list[int]:
    from sqlalchemy import select

    from app.db.models import TelegramUser
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        rows = await session.execute(
            select(TelegramUser.telegram_id).where(segment_filter(seg)).order_by(TelegramUser.telegram_id))
        return [r[0] for r in rows.all()]
