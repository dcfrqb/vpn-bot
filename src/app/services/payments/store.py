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

REFUND_ACCESS_TERMINAL = frozenset({"applied", "manual_review"})


def refund_notice_due(meta, now):
    marker = meta.get(M_REFUND_24H) or {}
    access = marker.get("access") or {}
    if not access.get("notice_pending"):
        return False
    stamp = access.get("notice_lease_until") or access.get("notice_due_at")
    return not stamp or _naive(datetime.fromisoformat(stamp)) <= _naive(now)


def queue_refund_notice(marker, phase):
    access = dict(marker.get("access") or {})
    if phase == "pending":
        phase = "retry" if marker.get("money_status") == "succeeded" else "pending_money"
    if phase == "awaiting_money" or (phase == "retry" and marker.get("money_status") != "succeeded"):
        phase = "pending_money"
    if phase != access.get("notice_phase"):
        access.update(notice_phase=phase, notice_pending=True, notice_user_delivered=False,
                      notice_admin_delivered=phase not in ("retry", "manual_review"))
        access.pop("notice_due_at", None)
    marker["access"] = access
    return marker


def claim_refund_notice_marker(meta, *, owner, now, lease_for):
    if not refund_notice_due(meta, now):
        return None
    marker = dict(meta[M_REFUND_24H])
    access = dict(marker["access"])
    access.update(notice_owner=owner, notice_lease_until=(_naive(now) + lease_for).isoformat())
    marker["access"] = access
    return marker


def finish_refund_notice_marker(meta, *, owner, phase, user_delivered, admin_delivered, now):
    marker = dict(meta.get(M_REFUND_24H) or {})
    access = dict(marker.get("access") or {})
    if access.get("notice_owner") != owner or access.get("notice_phase") != phase:
        return None
    access["notice_user_delivered"] = bool(access.get("notice_user_delivered") or user_delivered)
    access["notice_admin_delivered"] = bool(access.get("notice_admin_delivered") or admin_delivered)
    access["notice_pending"] = not (access["notice_user_delivered"] and access["notice_admin_delivered"])
    access.pop("notice_owner", None)
    access.pop("notice_lease_until", None)
    if access["notice_pending"]:
        access["notice_due_at"] = (_naive(now) + timedelta(minutes=5)).isoformat()
    marker["access"] = access
    return marker


def confirmed_refund_marker(meta, *, refund_id, now):
    """Only the verified provider webhook calls this, inside its existing lock."""
    marker = merge_refund_marker(meta, {"money_status": "succeeded", "provider_refund_id": refund_id, "state": "done"})
    access = dict(marker.get("access") or {})
    rearm = (access.get("state") == "manual_review" and access.get("reason_class") == "money_unconfirmed")
    if (not marker.get("revoked") and (rearm or access.get("state") in (None, "pending", "awaiting_money", "retry"))):
        access.update(state="pending", due_at=_naive(now).isoformat())
        marker["access"] = access
    return marker


def refund_access_due(meta: Mapping[str, Any], now: datetime) -> bool:
    marker = meta.get(M_REFUND_24H)
    if not isinstance(marker, dict) or marker.get("revoked") is True:
        return False
    access = marker.get("access") or {}
    if not isinstance(access, dict):
        return False
    state = access.get("state")
    if state in REFUND_ACCESS_TERMINAL:
        return False
    if state is None and marker.get("state") not in ("done", "refunding", "pending_money"):
        return False
    if state not in (None, "pending", "retry", "awaiting_money", "processing"):
        return False
    value = access.get("lease_until") if state == "processing" else access.get("due_at")
    if value:
        try:
            return _naive(datetime.fromisoformat(value)) <= _naive(now)
        except (TypeError, ValueError):
            return False
    return True


def merge_refund_marker(meta, patch, access_patch=None):
    """Merge under the payment row lock; never overwrite another owner's work."""
    marker = dict(meta.get(M_REFUND_24H) or {})
    updates = dict(patch)
    if marker.get("money_status") == "succeeded":
        updates.pop("money_status", None)
    marker.update(updates)
    access = dict(marker.get("access") or {})
    can_rearm = (marker.get("money_status") == "succeeded" and access.get("state") == "manual_review"
                 and access.get("reason_class") == "money_unconfirmed")
    if access_patch and (access.get("state") not in (*REFUND_ACCESS_TERMINAL, "processing") or can_rearm):
        access.update(access_patch)
        marker["access"] = access
        if access.get("state") == "awaiting_money":
            marker = queue_refund_notice(marker, "pending_money")
            access = marker["access"]
    if marker.get("money_status") == "succeeded":
        marker["state"] = "done"
    if access.get("state") == "applied":
        marker["revoked"] = True
    return marker


def claim_refund_marker(meta, *, owner, now, lease_for):
    if not refund_access_due(meta, now):
        return None
    marker = dict(meta[M_REFUND_24H])
    access = dict(marker.get("access") or {})
    access.update(state="processing", lease_owner=owner,
                  lease_until=(_naive(now) + lease_for).isoformat(),
                  attempts=int(access.get("attempts") or 0) + 1)
    marker["access"] = access
    return marker


def finish_refund_marker(meta, *, owner, state, now, error="", action=None, reason_class=None):
    marker = dict(meta.get(M_REFUND_24H) or {})
    access = dict(marker.get("access") or {})
    if access.get("state") != "processing" or access.get("lease_owner") != owner:
        return None
    # A verified webhook/direct response can confirm money while this owner
    # awaits an older provider read. Its stale snapshot cannot close the queue.
    rearmed = state == "manual_review" and reason_class == "money_unconfirmed" and marker.get("money_status") == "succeeded"
    if rearmed:
        state, error, reason_class = "pending", "", None
    access.update(state=state, last_error=error[:200], action=action, reason_class=reason_class)
    access.pop("lease_owner", None)
    access.pop("lease_until", None)
    if state not in REFUND_ACCESS_TERMINAL:
        minutes = min(360, 5 * 2 ** min(int(access.get("attempts") or 1) - 1, 7))
        access["due_at"] = (_naive(now) if rearmed else _naive(now) + timedelta(minutes=minutes)).isoformat()
    else:
        access.pop("due_at", None)
        access["completed_at"] = _naive(now).isoformat()
    marker["access"] = access
    if marker.get("money_status") == "succeeded":
        marker["state"] = "done"
    marker["revoked"] = state == "applied"
    return queue_refund_notice(marker, state)


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
    async def refund_access_candidates(self, now: datetime, *, limit: int = 20) -> list[PaymentRecord]: ...
    async def update_refund_marker(self, payment_id: int, patch: Mapping[str, Any], *,
                                   access_patch: Optional[Mapping[str, Any]] = None) -> bool: ...
    async def claim_refund_access(self, payment_id: int, *, owner: str, now: datetime,
                                  lease_for: timedelta) -> Optional[PaymentRecord]: ...
    async def finish_refund_access(self, payment_id: int, *, owner: str, state: str, now: datetime,
                                   error: str = "", action: Optional[str] = None,
                                   reason_class: Optional[str] = None) -> Optional[PaymentRecord]: ...
    async def claim_refund_notice(self, payment_id: int, *, owner: str, now: datetime,
                                  lease_for: timedelta) -> Optional[PaymentRecord]: ...
    async def finish_refund_notice(self, payment_id: int, *, owner: str, phase: str,
                                   user_delivered: bool, admin_delivered: bool, now: datetime) -> bool: ...
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
