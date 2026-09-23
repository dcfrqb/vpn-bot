"""Admin read models (stream E): bot stats, user card for /whois, payment-request log.

Stats count only main subscriptions (sub_kind == "main") and only customer
money (providers promo/test/referral_payout/admin excluded).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from app.services.promo import NON_REVENUE_PROVIDERS


@dataclass(frozen=True)
class BotStats:
    total_users: int = 0
    today_users: int = 0
    active_subscriptions: int = 0
    trials_total: int = 0
    trials_today: int = 0
    paid_total: int = 0
    paid_today: int = 0
    revenue_total: float = 0.0
    revenue_today: float = 0.0
    revenue_30d: float = 0.0
    refunded_total: float = 0.0


def _session():
    from app.db.session import SessionLocal

    if SessionLocal is None:
        return None
    return SessionLocal()


async def bot_stats(now: Optional[datetime] = None) -> BotStats:
    from sqlalchemy import func, or_, select

    from app.db.models import Payment, Subscription, TelegramUser, Trial

    s = _session()
    if s is None:
        return BotStats()
    now = now or datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    real = (Payment.status == "succeeded", Payment.provider.notin_(NON_REVENUE_PROVIDERS))
    async with s:
        async def one(q):
            return (await s.execute(q)).scalar() or 0

        return BotStats(
            total_users=await one(select(func.count(TelegramUser.telegram_id))),
            today_users=await one(select(func.count(TelegramUser.telegram_id)).where(TelegramUser.created_at >= today)),
            active_subscriptions=await one(select(func.count(Subscription.id)).where(
                Subscription.sub_kind == "main", Subscription.active.is_(True),
                or_(Subscription.valid_until.is_(None), Subscription.valid_until > now))),
            trials_total=await one(select(func.count(Trial.id))),
            trials_today=await one(select(func.count(Trial.id)).where(Trial.started_at >= today)),
            paid_total=await one(select(func.count(Payment.id)).where(*real)),
            paid_today=await one(select(func.count(Payment.id)).where(*real, Payment.paid_at >= today)),
            revenue_total=float(await one(select(func.coalesce(func.sum(Payment.amount), 0)).where(*real))),
            revenue_today=float(await one(select(func.coalesce(func.sum(Payment.amount), 0)).where(
                *real, Payment.paid_at >= today))),
            revenue_30d=float(await one(select(func.coalesce(func.sum(Payment.amount), 0)).where(
                *real, Payment.paid_at >= now - timedelta(days=30)))),
            refunded_total=float(await one(select(func.coalesce(func.sum(Payment.refunded_amount), 0)).where(
                Payment.provider.notin_(NON_REVENUE_PROVIDERS)))),
        )


@dataclass(frozen=True)
class PaymentLine:
    id: int
    provider: str
    status: str
    amount: float
    plan_code: Optional[str]
    months: Optional[int]
    paid_at: Optional[datetime]


@dataclass(frozen=True)
class UserCard:
    telegram_id: int
    known: bool = False
    username: Optional[str] = None
    name: str = ""
    created_at: Optional[datetime] = None
    is_active: bool = True
    opt_out: bool = False
    panel_id: Optional[str] = None
    trial_at: Optional[datetime] = None
    promos: list[str] = field(default_factory=list)
    payments: list[PaymentLine] = field(default_factory=list)
    paid_sum: float = 0.0


async def user_card(telegram_id: int) -> UserCard:
    from sqlalchemy import func, select

    from app.db.models import Payment, PromoRedemption, TelegramUser, Trial

    tg = int(telegram_id)
    s = _session()
    if s is None:
        return UserCard(tg)
    async with s:
        u = await s.get(TelegramUser, tg)
        if u is None:
            return UserCard(tg)
        trial = (await s.execute(select(Trial.started_at).where(Trial.telegram_user_id == tg))).scalar_one_or_none()
        promos = [r[0] for r in (await s.execute(select(PromoRedemption.code).where(
            PromoRedemption.telegram_user_id == tg, PromoRedemption.status == "applied")
            .order_by(PromoRedemption.id.desc()).limit(10))).all()]
        pays = (await s.execute(select(Payment).where(Payment.telegram_user_id == tg)
                                .order_by(Payment.id.desc()).limit(5))).scalars().all()
        paid = (await s.execute(select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.telegram_user_id == tg, Payment.status == "succeeded",
            Payment.provider.notin_(NON_REVENUE_PROVIDERS)))).scalar() or 0
    name = " ".join(x for x in (u.first_name, u.last_name) if x)
    return UserCard(
        tg, known=True, username=u.username, name=name, created_at=u.created_at, is_active=bool(u.is_active),
        opt_out=bool(u.broadcast_opt_out), panel_id=u.remna_user_id, trial_at=trial, promos=promos,
        payments=[PaymentLine(p.id, p.provider, p.status, float(p.amount or 0),
                              p.plan_code or (p.payment_metadata or {}).get("plan_code"),
                              p.period_months or (p.payment_metadata or {}).get("period_months"), p.paid_at)
                  for p in pays],
        paid_sum=float(paid),
    )


def _payments_log(settings: Any = None) -> Path:
    if settings is None:
        from app.config import settings
    base = getattr(settings, "LOG_DIR", None) or str(Path(__file__).resolve().parents[3] / "logs")
    return Path(base) / "payments.jsonl"


def payment_requests(*, req_id: Optional[str] = None, limit: int = 10, settings: Any = None) -> list[dict]:
    """2.x /payments_new and /payment_find: records from logs/payments.jsonl."""
    path = _payments_log(settings)
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if req_id is not None:
                if rec.get("req_id") == req_id:
                    out.append(rec)
            elif rec.get("event") == "payment_request_created":
                out.append(rec)
    return out if req_id is not None else out[-limit:]


__all__ = ["BotStats", "bot_stats", "UserCard", "PaymentLine", "user_card", "payment_requests"]
