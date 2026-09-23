"""Money persistence (release 3.0, stream A): payments, refund_requests,
payment_methods and the autorenew fields of the main subscription.

Services talk to ``PaymentStore`` (Protocol) and get plain records back, so
the state machine is unit-tested with an in-memory store
(tests/money/fake_store.py) and the SQL here is tested on real Postgres
(tests/integration/test_money_store_real_postgres.py).

Every state change is a compare-and-set under ``SELECT ... FOR UPDATE``:
two processes (bot and webhook API) can race on one payment, and only one of
them moves it. Money is never computed here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence

from app.logger import logger

# Payment meta keys shared with the 2.x code (recovery, review, refunds read them).
M_NEEDS_REVIEW = "needs_review"
M_REVIEW_APPROVED = "review_approved"
M_REVIEW_REJECTED = "review_rejected"
M_NEEDS_PROVISIONING = "needs_provisioning"
M_NOTIFIED = "notified"
M_ADMIN_NOTIFIED = "admin_notified"
M_OBHOD_APPLIED = "obhod_package_applied"
M_FULFILLED_AT = "fulfilled_at"

PENDING_STATUSES = ("pending", "waiting_for_capture")


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone

        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@dataclass
class PaymentRecord:
    id: int
    telegram_id: int
    provider: str
    external_id: str
    amount: Decimal
    currency: str
    status: str
    plan_code: Optional[str] = None
    period_months: Optional[int] = None
    kind: str = "subscription"
    method: str = "yookassa"
    meta: dict = field(default_factory=dict)
    subscription_id: Optional[int] = None
    created_at: Optional[datetime] = None  # naive UTC, as in the DB
    paid_at: Optional[datetime] = None
    telegram_charge_id: Optional[str] = None
    refunded_amount: Optional[Decimal] = None
    description: Optional[str] = None

    @property
    def fulfilled(self) -> bool:
        """Access (or the gift/package) was delivered for this payment.

        2.x rows: subscription_id is set only after Remnawave confirmed the
        period (Phase C); obhod packages carry obhod_package_applied."""
        m = self.meta
        return bool(m.get(M_FULFILLED_AT)) or self.subscription_id is not None or m.get(M_OBHOD_APPLIED) is not None

    @property
    def confirmation_url(self) -> Optional[str]:
        return self.meta.get("confirmation_url")

    @property
    def autorenew(self) -> bool:
        return bool(self.meta.get("autorenew"))

    @property
    def expected_stars(self) -> Optional[int]:
        return _int(self.meta.get("expected_stars"))


@dataclass
class RefundRequestRecord:
    id: int
    payment_id: int
    telegram_id: int
    status: str  # pending | approved | rejected | refunded | failed
    reason: Optional[str] = None
    amount: Optional[Decimal] = None
    decided_by: Optional[int] = None
    decided_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


@dataclass
class SavedMethodRecord:
    id: int
    telegram_id: int
    provider: str
    external_id: str
    title: Optional[str] = None
    is_active: bool = True


@dataclass
class SubInfo:
    """The main subscription row (sub_kind='main') as money code needs it."""

    id: int
    telegram_id: int
    plan_code: Optional[str]
    active: bool
    valid_until: Optional[datetime]
    is_lifetime: bool = False
    autorenew: bool = False
    autorenew_method_id: Optional[int] = None


class PaymentStore(Protocol):
    async def get(self, payment_id: int) -> Optional[PaymentRecord]: ...
    async def get_by_external(self, external_id: str) -> Optional[PaymentRecord]: ...
    async def find_reusable(self, telegram_id: int, *, plan_code: str, months: int, kind: str, method: str,
                            autorenew: bool, since: datetime) -> Optional[PaymentRecord]: ...
    async def create(self, telegram_id: int, *, provider: str, external_id: str, amount: Decimal, currency: str,
                     status: str, plan_code: Optional[str], months: Optional[int], kind: str, method: str,
                     description: str, meta: Mapping[str, Any],
                     user: Optional[Mapping[str, Any]] = None) -> PaymentRecord: ...
    async def mark_paid(self, payment_id: int, *, amount: Optional[Decimal] = None,
                        charge_id: Optional[str] = None, card_fingerprint: Optional[str] = None,
                        meta_patch: Optional[Mapping[str, Any]] = None) -> Optional[PaymentRecord]: ...
    async def set_status(self, payment_id: int, from_statuses: Iterable[str], to_status: str,
                         meta_patch: Optional[Mapping[str, Any]] = None) -> bool: ...
    async def patch_meta(self, payment_id: int, patch: Mapping[str, Any], *, claim: Optional[str] = None,
                         drop: Sequence[str] = ()) -> bool: ...
    async def mark_fulfilled(self, payment_id: int, *, meta_patch: Optional[Mapping[str, Any]] = None) -> None: ...
    async def payer_stats(self, telegram_id: int) -> tuple[int, Decimal]: ...
    async def user_names(self, telegram_id: int) -> tuple[str, str, str]: ...
    async def recovery_candidates(self, now: datetime, *, pending_age: timedelta, stuck_age: timedelta,
                                  horizon: timedelta, limit: int) -> tuple[list[PaymentRecord], list[PaymentRecord]]: ...
    async def create_refund_request(self, payment_id: int, telegram_id: int, *, amount: Optional[Decimal],
                                    reason: str) -> tuple[RefundRequestRecord, bool]: ...
    async def get_refund_request(self, request_id: int) -> Optional[RefundRequestRecord]: ...
    async def refund_request_for_payment(self, payment_id: int) -> Optional[RefundRequestRecord]: ...
    async def transition_refund_request(self, request_id: int, from_statuses: Iterable[str], to_status: str, *,
                                        decided_by: Optional[int] = None) -> Optional[RefundRequestRecord]: ...
    async def save_method(self, telegram_id: int, *, provider: str, external_id: str, title: Optional[str],
                          card_fingerprint: Optional[str]) -> int: ...
    async def get_method(self, method_id: int) -> Optional[SavedMethodRecord]: ...
    async def main_subscription(self, telegram_id: int) -> Optional[SubInfo]: ...
    async def set_autorenew(self, telegram_id: int, enabled: bool, *, method_id: Optional[int] = None) -> bool: ...
    async def autorenew_subscriptions(self, until: datetime) -> list[SubInfo]: ...
    async def autorenew_attempts(self, telegram_id: int, period_key: str) -> list[PaymentRecord]: ...
    async def last_paid_subscription(self, telegram_id: int) -> Optional[PaymentRecord]: ...


# ---------------------------------------------------------------------------
# SQL implementation
# ---------------------------------------------------------------------------


def _record(row: Any) -> PaymentRecord:
    from app.domain.plans import is_obhod_package_code

    meta = dict(row.payment_metadata) if isinstance(row.payment_metadata, dict) else {}
    plan = row.plan_code or meta.get("plan_code")
    months = row.period_months or _int(meta.get("period_months"))
    kind = row.kind or ("obhod_package" if is_obhod_package_code(plan) else "subscription")
    method = row.method or ("stars" if row.provider == "stars" else "yookassa")
    return PaymentRecord(
        id=row.id,
        telegram_id=int(row.telegram_user_id),
        provider=row.provider,
        external_id=row.external_id,
        amount=Decimal(str(row.amount)) if row.amount is not None else Decimal("0"),
        currency=(row.currency or "RUB").upper(),
        status=row.status,
        plan_code=plan,
        period_months=months,
        kind=kind,
        method=method,
        meta=meta,
        subscription_id=row.subscription_id,
        created_at=row.created_at,
        paid_at=row.paid_at,
        telegram_charge_id=row.telegram_charge_id,
        refunded_amount=Decimal(str(row.refunded_amount)) if row.refunded_amount is not None else None,
        description=row.description,
    )


def _refund_record(row: Any) -> RefundRequestRecord:
    return RefundRequestRecord(
        id=row.id, payment_id=row.payment_id, telegram_id=int(row.telegram_user_id), status=row.status,
        reason=row.reason, amount=Decimal(str(row.amount)) if row.amount is not None else None,
        decided_by=row.decided_by, decided_at=row.decided_at, created_at=row.created_at,
    )


def _sub_info(row: Any) -> SubInfo:
    return SubInfo(
        id=row.id, telegram_id=int(row.telegram_user_id), plan_code=row.plan_code, active=bool(row.active),
        valid_until=row.valid_until, is_lifetime=bool(row.is_lifetime), autorenew=bool(row.autorenew),
        autorenew_method_id=row.autorenew_method_id,
    )


class SqlPaymentStore:
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
                payment_metadata=dict(meta), plan_code=plan_code, period_months=months, kind=kind, method=method,
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
            m = rec.meta
            if m.get(M_REVIEW_REJECTED) or (m.get(M_NEEDS_REVIEW) and not m.get(M_REVIEW_APPROVED)):
                continue
            since = rec.paid_at or rec.created_at
            if m.get(M_NEEDS_PROVISIONING) or (since is not None and since < now - stuck_age):
                stuck.append(rec)
        return [_record(r) for r in pending], stuck[:limit]

    # --- refund requests --------------------------------------------------------------------

    async def create_refund_request(self, payment_id: int, telegram_id: int, *, amount: Optional[Decimal],
                                    reason: str) -> tuple[RefundRequestRecord, bool]:
        from sqlalchemy.exc import IntegrityError

        from app.db.models import RefundRequest

        existing = await self.refund_request_for_payment(payment_id)
        if existing is not None:
            return existing, False
        async with self._session() as s:
            row = RefundRequest(payment_id=int(payment_id), telegram_user_id=int(telegram_id), status="pending",
                                reason=reason, amount=amount)
            s.add(row)
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()
                again = await self.refund_request_for_payment(payment_id)
                if again is None:
                    raise
                return again, False
            await s.refresh(row)
            return _refund_record(row), True

    async def get_refund_request(self, request_id: int) -> Optional[RefundRequestRecord]:
        from sqlalchemy import select

        from app.db.models import RefundRequest

        async with self._session() as s:
            row = (await s.execute(
                select(RefundRequest).where(RefundRequest.id == int(request_id)))).scalar_one_or_none()
            return _refund_record(row) if row else None

    async def refund_request_for_payment(self, payment_id: int) -> Optional[RefundRequestRecord]:
        from sqlalchemy import select

        from app.db.models import RefundRequest

        async with self._session() as s:
            row = (await s.execute(
                select(RefundRequest).where(RefundRequest.payment_id == int(payment_id))
                .order_by(RefundRequest.id.desc()).limit(1)
            )).scalar_one_or_none()
            return _refund_record(row) if row else None

    async def transition_refund_request(self, request_id: int, from_statuses: Iterable[str], to_status: str, *,
                                        decided_by: Optional[int] = None) -> Optional[RefundRequestRecord]:
        from sqlalchemy import select

        from app.db.models import RefundRequest

        async with self._session() as s:
            row = (await s.execute(
                select(RefundRequest).where(RefundRequest.id == int(request_id)).with_for_update()
            )).scalar_one_or_none()
            if row is None or row.status not in set(from_statuses):
                return None
            row.status = to_status
            if decided_by is not None:
                row.decided_by = int(decided_by)
                row.decided_at = datetime.utcnow()
            await s.commit()
            return _refund_record(row)

    # --- saved methods and autorenew --------------------------------------------------------

    async def save_method(self, telegram_id: int, *, provider: str, external_id: str, title: Optional[str],
                          card_fingerprint: Optional[str]) -> int:
        from sqlalchemy import select
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.models import SavedPaymentMethod

        async with self._session() as s:
            await s.execute(
                pg_insert(SavedPaymentMethod).values(
                    telegram_user_id=int(telegram_id), provider=provider, external_id=str(external_id),
                    title=title, card_fingerprint=card_fingerprint, is_active=True,
                ).on_conflict_do_update(
                    index_elements=["provider", "external_id"],
                    set_={"is_active": True, "revoked_at": None, "title": title},
                )
            )
            mid = (await s.execute(select(SavedPaymentMethod.id).where(
                SavedPaymentMethod.provider == provider, SavedPaymentMethod.external_id == str(external_id),
            ))).scalar_one()
            await s.commit()
            return int(mid)

    async def get_method(self, method_id: int) -> Optional[SavedMethodRecord]:
        from sqlalchemy import select

        from app.db.models import SavedPaymentMethod

        async with self._session() as s:
            row = (await s.execute(
                select(SavedPaymentMethod).where(SavedPaymentMethod.id == int(method_id)))).scalar_one_or_none()
        if row is None:
            return None
        return SavedMethodRecord(id=row.id, telegram_id=int(row.telegram_user_id), provider=row.provider,
                                 external_id=row.external_id, title=row.title, is_active=bool(row.is_active))

    async def main_subscription(self, telegram_id: int) -> Optional[SubInfo]:
        from sqlalchemy import select

        from app.db.models import Subscription

        async with self._session() as s:
            row = (await s.execute(select(Subscription).where(
                Subscription.telegram_user_id == int(telegram_id), Subscription.sub_kind == "main",
            ))).scalar_one_or_none()
            return _sub_info(row) if row else None

    async def set_autorenew(self, telegram_id: int, enabled: bool, *, method_id: Optional[int] = None) -> bool:
        from sqlalchemy import select

        from app.db.models import Subscription

        async with self._session() as s:
            row = (await s.execute(select(Subscription).where(
                Subscription.telegram_user_id == int(telegram_id), Subscription.sub_kind == "main",
            ).with_for_update())).scalar_one_or_none()
            if row is None:
                return False
            row.autorenew = bool(enabled)
            if enabled and method_id is not None:
                row.autorenew_method_id = int(method_id)
            await s.commit()
            logger.info(f"autorenew {'on' if enabled else 'off'}: tg_id={telegram_id}")
            return True

    async def autorenew_subscriptions(self, until: datetime) -> list[SubInfo]:
        from sqlalchemy import select

        from app.db.models import Subscription

        async with self._session() as s:
            rows = (await s.execute(select(Subscription).where(
                Subscription.sub_kind == "main",
                Subscription.autorenew.is_(True),
                Subscription.active.is_(True),
                Subscription.is_lifetime.is_(False),
                Subscription.valid_until.isnot(None),
                Subscription.valid_until <= _naive(until),
            ))).scalars().all()
        return [_sub_info(r) for r in rows]

    async def autorenew_attempts(self, telegram_id: int, period_key: str) -> list[PaymentRecord]:
        from sqlalchemy import select

        from app.db.models import Payment

        async with self._session() as s:
            rows = (await s.execute(select(Payment).where(
                Payment.telegram_user_id == int(telegram_id),
                Payment.kind == "autorenew",
            ).order_by(Payment.id))).scalars().all()
        return [r for r in (_record(x) for x in rows) if r.meta.get("period_key") == period_key]

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
