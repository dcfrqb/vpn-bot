"""Money persistence (release 3.0, stream A): payments, refund_requests,
payment_methods and the autorenew fields of the main subscription.

Services talk to ``PaymentStore`` (Protocol) and get plain records back, so
the state machine is unit-tested with an in-memory store (tests/money/fakes.py)
and the SQL implementation (app.services.payments.sql_store.SqlPaymentStore)
is tested on real Postgres (tests/integration/test_money_real_postgres.py).

Every state change is a compare-and-set under ``SELECT ... FOR UPDATE``:
two processes (bot and webhook API) can race on one payment, and only one of
them moves it. Money is never computed here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence


# Payment meta keys shared with the 2.x code (recovery, review, refunds read them).
M_NEEDS_REVIEW = "needs_review"
M_REVIEW_APPROVED = "review_approved"
M_REVIEW_REJECTED = "review_rejected"
M_NEEDS_PROVISIONING = "needs_provisioning"
M_NOTIFIED = "notified"
M_ADMIN_NOTIFIED = "admin_notified"
M_OBHOD_APPLIED = "obhod_package_applied"
M_FULFILLED_AT = "fulfilled_at"
# Set by PaymentStore.create (3.0 rows). Recovery retries a paid-but-unlinked
# row without the needs_provisioning flag ONLY when it carries this marker:
# 2.x rows (subscription_id may be NULL on rows 2.x did deliver) are retried
# only when 2.x itself flagged them, exactly as 2.1 did. No double grants on
# the first 3.0 deploy.
M_CREATED_V3 = "v3"
# Written by the 24h refund flow before the money goes back: the payment is
# never granted after that (fulfillment and recovery skip it).
M_REFUND_24H = "refund_24h"


def stuck_is_recoverable(meta: Mapping[str, Any], since: Optional[datetime], now: datetime,
                         stuck_age: timedelta) -> bool:
    """A paid, unfulfilled, not held row the recovery sweep should grant again."""
    if meta.get(M_REVIEW_REJECTED) or (meta.get(M_NEEDS_REVIEW) and not meta.get(M_REVIEW_APPROVED)):
        return False
    if meta.get(M_REFUND_24H):
        return False  # review money M-2: refunded money is never granted by the sweep
    if meta.get(M_NEEDS_PROVISIONING):
        return True
    return bool(meta.get(M_CREATED_V3)) and since is not None and since < now - stuck_age

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


def __getattr__(name: str):  # SqlPaymentStore lives in sql_store (module size); old import path kept
    if name == "SqlPaymentStore":
        from app.services.payments.sql_store import SqlPaymentStore

        return SqlPaymentStore
    raise AttributeError(name)
