"""Refund requests, saved payment methods and autorenew in SQL (release 3.0, A).

Mixed into app.services.payments.sql_store.SqlPaymentStore; also holds the
row -> record converters shared by both modules.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Optional

from app.logger import logger
from app.services.payments.store import (
    M_REFUND_24H,
    claim_refund_marker,
    claim_refund_notice_marker,
    finish_refund_notice_marker,
    finish_refund_marker,
    merge_refund_marker,
    PaymentRecord,
    RefundRequestRecord,
    SavedMethodRecord,
    SubInfo,
    _int,
    _naive,
)


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


class SqlRefundsAutorenewMixin:
    """Needs ``self._session()`` (SqlPaymentStore)."""

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

    async def refund_access_candidates(self, now: datetime, *, limit: int = 20) -> list[PaymentRecord]:
        from sqlalchemy import and_, case, or_, select
        from app.db.models import Payment

        marker = Payment.payment_metadata.op("->")(M_REFUND_24H)
        access = marker.op("->")("access")
        state = access.op("->>")("state")
        due, lease = access.op("->>")("due_at"), access.op("->>")("lease_until")
        stamp = _naive(now).isoformat()
        ready = and_(or_(state.in_(("pending", "retry", "awaiting_money")),
                         and_(state.is_(None), marker.op("->>")("state").in_(("done", "refunding", "pending_money")))),
                     or_(due.is_(None), due <= stamp))
        expired = and_(state == "processing", or_(lease.is_(None), lease <= stamp))
        notice_due = access.op("->>")("notice_due_at")
        notice_lease = access.op("->>")("notice_lease_until")
        notices = and_(access.op("->>")("notice_pending") == "true",
                       or_(notice_due.is_(None), notice_due <= stamp),
                       or_(notice_lease.is_(None), notice_lease <= stamp))
        main = and_(or_(marker.op("->>")("revoked").is_(None), marker.op("->>")("revoked") != "true"),
                    or_(ready, expired))
        async with self._session() as s:
            rows = (await s.execute(select(Payment).where(
                marker.isnot(None), or_(main, notices),
            ).order_by(case((main, 0), else_=1), Payment.id).limit(limit))).scalars().all()
            return [_record(r) for r in rows]

    async def update_refund_marker(self, payment_id, patch, *, access_patch=None):
        async with self._session() as s:
            row = await self._locked(s, payment_id)
            if row is None:
                return False
            meta = dict(row.payment_metadata or {})
            meta[M_REFUND_24H] = merge_refund_marker(meta, patch, access_patch)
            row.payment_metadata = meta
            await s.commit()
            return True

    async def claim_refund_access(self, payment_id, *, owner, now, lease_for):
        async with self._session() as s:
            row = await self._locked(s, payment_id)
            if row is None:
                return None
            meta = dict(row.payment_metadata or {})
            marker = claim_refund_marker(meta, owner=owner, now=now, lease_for=lease_for)
            if marker is None:
                return None
            meta[M_REFUND_24H] = marker
            row.payment_metadata = meta
            await s.commit()
            return _record(row)

    async def finish_refund_access(self, payment_id, *, owner, state, now, error="", action=None, reason_class=None):
        async with self._session() as s:
            row = await self._locked(s, payment_id)
            if row is None:
                return None
            meta = dict(row.payment_metadata or {})
            marker = finish_refund_marker(meta, owner=owner, state=state, now=now, error=error, action=action, reason_class=reason_class)
            if marker is None:
                return None
            meta[M_REFUND_24H] = marker
            row.payment_metadata = meta
            await s.commit()
            return _record(row)

    async def claim_refund_notice(self, payment_id, *, owner, now, lease_for):
        async with self._session() as s:
            row = await self._locked(s, payment_id)
            if row is None:
                return None
            meta = dict(row.payment_metadata or {})
            marker = claim_refund_notice_marker(meta, owner=owner, now=now, lease_for=lease_for)
            if marker is None:
                return None
            meta[M_REFUND_24H] = marker
            row.payment_metadata = meta
            await s.commit()
            return _record(row)

    async def finish_refund_notice(self, payment_id, *, owner, phase, user_delivered, admin_delivered, now):
        async with self._session() as s:
            row = await self._locked(s, payment_id)
            if row is None:
                return False
            meta = dict(row.payment_metadata or {})
            marker = finish_refund_notice_marker(meta, owner=owner, phase=phase, user_delivered=user_delivered,
                                                admin_delivered=admin_delivered, now=now)
            if marker is None:
                return False
            meta[M_REFUND_24H] = marker
            row.payment_metadata = meta
            await s.commit()
            return True

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

