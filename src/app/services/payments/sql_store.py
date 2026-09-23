"""SQL implementation of app.services.payments.store.PaymentStore (release 3.0, A).

Every state change is a compare-and-set under ``SELECT ... FOR UPDATE``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.services.payments.sql_store_refunds import SqlRefundsAutorenewMixin, _record
from app.services.payments.store import (
    M_CREATED_V3,
    M_FULFILLED_AT,
    M_NEEDS_PROVISIONING,
    PENDING_STATUSES,
    PaymentRecord,
    _naive,
    stuck_is_recoverable,
)


class SqlPaymentStore(SqlRefundsAutorenewMixin):
    """PaymentStore over app.db (SQLAlchemy async)."""

    def __init__(self, session_factory: Any = None):
        self._factory = session_factory

    def _session(self):
        factory = self._factory
        if factory is None:
            from app.db import session as db_session

            factory = db_session.SessionLocal
        if factory is None:
            raise RuntimeError("database is not configured")
        return factory()

    # --- payments ---------------------------------------------------------------------------

    async def get(self, payment_id: int) -> Optional[PaymentRecord]:
        from sqlalchemy import select

        from app.db.models import Payment

        async with self._session() as s:
            row = (await s.execute(select(Payment).where(Payment.id == int(payment_id)))).scalar_one_or_none()
            return _record(row) if row else None

    async def get_by_external(self, external_id: str) -> Optional[PaymentRecord]:
        from sqlalchemy import select

        from app.db.models import Payment

        async with self._session() as s:
            row = (await s.execute(select(Payment).where(Payment.external_id == str(external_id)))).scalar_one_or_none()
            return _record(row) if row else None

    async def find_reusable(self, telegram_id: int, *, plan_code: str, months: int, kind: str, method: str,
                            autorenew: bool, since: datetime) -> Optional[PaymentRecord]:
        from sqlalchemy import select

        from app.db.models import Payment

        async with self._session() as s:
            rows = (await s.execute(
                select(Payment)
                .where(
                    Payment.telegram_user_id == int(telegram_id),
                    Payment.status == "pending",
                    Payment.plan_code == plan_code,
                    Payment.period_months == int(months),
                    Payment.kind == kind,
                    Payment.method == method,
                    Payment.created_at >= _naive(since),
                )
                .order_by(Payment.created_at.desc())
                .limit(5)
            )).scalars().all()
        for row in rows:
            rec = _record(row)
            if rec.autorenew == bool(autorenew) and (method == "stars" or rec.confirmation_url):
                return rec
        return None

    async def create(self, telegram_id: int, *, provider: str, external_id: str, amount: Decimal, currency: str,
                     status: str, plan_code: Optional[str], months: Optional[int], kind: str, method: str,
                     description: str, meta: Mapping[str, Any],
                     user: Optional[Mapping[str, Any]] = None) -> PaymentRecord:
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.exc import IntegrityError

        from app.db.models import Payment, TelegramUser

        values = {"telegram_id": int(telegram_id)}
        for key in ("username", "first_name", "last_name"):
            if user and user.get(key):
                values[key] = user[key]
        async with self._session() as s:
            stmt = pg_insert(TelegramUser).values(**values)
            extra = {k: v for k, v in values.items() if k != "telegram_id"}
            stmt = (stmt.on_conflict_do_update(index_elements=["telegram_id"], set_=extra) if extra
                    else stmt.on_conflict_do_nothing(index_elements=["telegram_id"]))
            await s.execute(stmt)
            row = Payment(
                telegram_user_id=int(telegram_id), provider=provider, external_id=str(external_id),
                amount=Decimal(str(amount)), currency=currency, status=status, description=description,
                payment_metadata={**dict(meta), M_CREATED_V3: True}, plan_code=plan_code, period_months=months, kind=kind, method=method,
            )
            s.add(row)
            try:
                await s.commit()
            except IntegrityError:
                # the same provider payment already recorded (idempotent create after a retry)
                await s.rollback()
                existing = (await s.execute(
                    select(Payment).where(Payment.external_id == str(external_id)))).scalar_one_or_none()
                if existing is None:
                    raise
                return _record(existing)
            await s.refresh(row)
            return _record(row)

    async def _locked(self, s, payment_id: int):
        from sqlalchemy import select

        from app.db.models import Payment

        return (await s.execute(
            select(Payment).where(Payment.id == int(payment_id)).with_for_update()
        )).scalar_one_or_none()

    async def mark_paid(self, payment_id: int, *, amount: Optional[Decimal] = None,
                        charge_id: Optional[str] = None, card_fingerprint: Optional[str] = None,
                        meta_patch: Optional[Mapping[str, Any]] = None) -> Optional[PaymentRecord]:
        async with self._session() as s:
            row = await self._locked(s, payment_id)
            if row is None:
                return None
            if row.status in PENDING_STATUSES:
                row.status = "succeeded"
                now = datetime.utcnow()
                row.paid_at = row.paid_at or now
                row.updated_at = now
                if amount is not None:
                    row.amount = Decimal(str(amount))
                if charge_id:
                    row.telegram_charge_id = charge_id
                if card_fingerprint:
                    row.card_fingerprint = card_fingerprint
                if meta_patch:
                    meta = dict(row.payment_metadata or {})
                    meta.update(meta_patch)
                    row.payment_metadata = meta
                await s.commit()
            return _record(row)

    async def set_status(self, payment_id: int, from_statuses: Iterable[str], to_status: str,
                         meta_patch: Optional[Mapping[str, Any]] = None) -> bool:
        async with self._session() as s:
            row = await self._locked(s, payment_id)
            if row is None or row.status not in set(from_statuses):
                return False
            row.status = to_status
            row.updated_at = datetime.utcnow()
            if meta_patch:
                meta = dict(row.payment_metadata or {})
                meta.update(meta_patch)
                row.payment_metadata = meta
            await s.commit()
            return True

    async def patch_meta(self, payment_id: int, patch: Mapping[str, Any], *, claim: Optional[str] = None,
                         drop: Sequence[str] = ()) -> bool:
        async with self._session() as s:
            row = await self._locked(s, payment_id)
            if row is None:
                return False
            meta = dict(row.payment_metadata or {}) if isinstance(row.payment_metadata, dict) else {}
            if claim is not None and meta.get(claim):
                return False
            meta.update(patch)
            if claim is not None:
                meta[claim] = True
            for key in drop:
                meta.pop(key, None)
            row.payment_metadata = meta
            await s.commit()
            return True

    async def mark_fulfilled(self, payment_id: int, *, meta_patch: Optional[Mapping[str, Any]] = None) -> None:
        from sqlalchemy import select

        from app.db.models import Subscription

        async with self._session() as s:
            row = await self._locked(s, payment_id)
            if row is None:
                return
            meta = dict(row.payment_metadata or {}) if isinstance(row.payment_metadata, dict) else {}
            meta[M_FULFILLED_AT] = meta.get(M_FULFILLED_AT) or datetime.utcnow().isoformat()
            meta.update(meta_patch or {})
            for key in (M_NEEDS_PROVISIONING, "provisioning_error", "provisioning_attempted_at"):
                meta.pop(key, None)
            row.payment_metadata = meta
            kind = row.kind or meta.get("kind")
            if row.subscription_id is None and kind in (None, "subscription", "autorenew"):
                sub = (await s.execute(
                    select(Subscription.id).where(
                        Subscription.telegram_user_id == row.telegram_user_id,
                        Subscription.sub_kind == "main",
                    )
                )).scalar_one_or_none()
                if sub is not None:
                    row.subscription_id = sub
            await s.commit()

    async def payer_stats(self, telegram_id: int) -> tuple[int, Decimal]:
        from sqlalchemy import func, select

        from app.db.models import Payment

        async with self._session() as s:
            count, total = (await s.execute(
                select(func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0)).where(
                    Payment.telegram_user_id == int(telegram_id),
                    Payment.status == "succeeded",
                    func.upper(Payment.currency) == "RUB",
                    Payment.provider.notin_(("test", "promo")),  # r30_03 relabels e2e junk as 'test' (F -> E)
                )
            )).one()
        return int(count or 0), Decimal(str(total or 0))

    async def user_names(self, telegram_id: int) -> tuple[str, str, str]:
        from sqlalchemy import select

        from app.db.models import TelegramUser

        async with self._session() as s:
            row = (await s.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == int(telegram_id)))).scalar_one_or_none()
        if row is None:
            return "", "", ""
        return (row.first_name or "").strip(), (row.last_name or "").strip(), (row.username or "").strip()

    async def recovery_candidates(self, now: datetime, *, pending_age: timedelta, stuck_age: timedelta,
                                  horizon: timedelta, limit: int) -> tuple[list[PaymentRecord], list[PaymentRecord]]:
        from sqlalchemy import select

        from app.db.models import Payment

        now = _naive(now)
        async with self._session() as s:
            pending = (await s.execute(
                select(Payment).where(
                    Payment.status.in_(PENDING_STATUSES),
                    Payment.provider == "yookassa",
                    Payment.created_at < now - pending_age,
                    Payment.created_at > now - horizon,
                ).order_by(Payment.created_at).limit(limit)
            )).scalars().all()
            paid = (await s.execute(
                select(Payment).where(
                    Payment.status == "succeeded",
                    Payment.provider.in_(("yookassa", "stars")),
                    Payment.subscription_id.is_(None),
                    Payment.created_at > now - horizon,
                ).order_by(Payment.created_at).limit(limit * 5)
            )).scalars().all()
        stuck = []
        for row in paid:
            rec = _record(row)
            if rec.fulfilled:
                continue
            if stuck_is_recoverable(rec.meta, rec.paid_at or rec.created_at, now, stuck_age):
                stuck.append(rec)
        return [_record(r) for r in pending], stuck[:limit]

    async def last_paid_subscription(self, telegram_id: int) -> Optional[PaymentRecord]:
        """Latest succeeded 3.0 subscription/autorenew payment (its plan and months are renewed)."""
        from sqlalchemy import select

        from app.db.models import Payment

        async with self._session() as s:
            row = (await s.execute(select(Payment).where(
                Payment.telegram_user_id == int(telegram_id),
                Payment.status == "succeeded",
                Payment.kind.in_(("subscription", "autorenew")),
                Payment.plan_code.isnot(None),
                Payment.period_months.isnot(None),
            ).order_by(Payment.id.desc()).limit(1))).scalar_one_or_none()
            return _record(row) if row else None
