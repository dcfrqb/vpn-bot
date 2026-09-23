"""Fakes for stream A (Money) tests: in-memory PaymentStore, provisioning, promo, hooks.

``InMemoryPaymentStore`` mirrors app.services.payments.store.SqlPaymentStore
(the SQL twin is exercised on real Postgres in
tests/integration/test_money_real_postgres.py).
"""
from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.domain.models import Entitlement, SubKind, SubscriptionState
from app.services.payments.store import (
    M_FULFILLED_AT,
    M_NEEDS_PROVISIONING,
    M_NEEDS_REVIEW,
    M_REVIEW_APPROVED,
    M_REVIEW_REJECTED,
    PENDING_STATUSES,
    PaymentRecord,
    RefundRequestRecord,
    SavedMethodRecord,
    SubInfo,
)

T0 = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start: datetime = T0):
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t = self.t + timedelta(**kw)


class InMemoryPaymentStore:
    def __init__(self, clock: Optional[Clock] = None):
        self.clock = clock or Clock()
        self.payments: dict[int, PaymentRecord] = {}
        self.requests: dict[int, RefundRequestRecord] = {}
        self.methods: dict[int, SavedMethodRecord] = {}
        self.subs: dict[int, SubInfo] = {}
        self.users: dict[int, tuple[str, str, str]] = {}
        self._pid = itertools.count(1)
        self._rid = itertools.count(1)
        self._mid = itertools.count(1)

    def _now(self) -> datetime:
        return self.clock().replace(tzinfo=None)

    def _copy(self, rec: Optional[PaymentRecord]) -> Optional[PaymentRecord]:
        return replace(rec, meta=dict(rec.meta)) if rec else None

    # --- helpers for tests ---
    def add_sub(self, telegram_id: int, **kw) -> SubInfo:
        sub = SubInfo(id=kw.pop("id", len(self.subs) + 1), telegram_id=telegram_id,
                      plan_code=kw.pop("plan_code", "standard"), active=kw.pop("active", True),
                      valid_until=kw.pop("valid_until", None), **kw)
        self.subs[telegram_id] = sub
        return sub

    def add_payment(self, telegram_id: int, **kw) -> PaymentRecord:
        pid = next(self._pid)
        rec = PaymentRecord(
            id=pid, telegram_id=telegram_id, provider=kw.pop("provider", "yookassa"),
            external_id=kw.pop("external_id", f"ext-{pid}"), amount=Decimal(str(kw.pop("amount", 0))),
            currency=kw.pop("currency", "RUB"), status=kw.pop("status", "pending"),
            created_at=kw.pop("created_at", self._now()), **kw,
        )
        self.payments[pid] = rec
        return self._copy(rec)

    # --- PaymentStore ---
    async def get(self, payment_id: int) -> Optional[PaymentRecord]:
        return self._copy(self.payments.get(int(payment_id)))

    async def get_by_external(self, external_id: str) -> Optional[PaymentRecord]:
        for rec in self.payments.values():
            if rec.external_id == external_id:
                return self._copy(rec)
        return None

    async def find_reusable(self, telegram_id, *, plan_code, months, kind, method, autorenew, since):
        since = since.replace(tzinfo=None) if since.tzinfo else since
        for rec in sorted(self.payments.values(), key=lambda r: r.id, reverse=True):
            if (rec.telegram_id == telegram_id and rec.status == "pending" and rec.plan_code == plan_code
                    and rec.period_months == months and rec.kind == kind and rec.method == method
                    and rec.created_at >= since and rec.autorenew == bool(autorenew)
                    and (method == "stars" or rec.confirmation_url)):
                return self._copy(rec)
        return None

    async def create(self, telegram_id, *, provider, external_id, amount, currency, status, plan_code, months, kind,
                     method, description, meta, user=None) -> PaymentRecord:
        existing = await self.get_by_external(external_id)
        if existing:
            return existing
        if user:
            self.users[telegram_id] = (user.get("first_name") or "", user.get("last_name") or "",
                                       user.get("username") or "")
        return self.add_payment(telegram_id, provider=provider, external_id=external_id, amount=amount,
                                currency=currency, status=status, plan_code=plan_code, period_months=months,
                                kind=kind, method=method, description=description, meta=dict(meta))

    async def mark_paid(self, payment_id, *, amount=None, charge_id=None, card_fingerprint=None, meta_patch=None):
        rec = self.payments.get(int(payment_id))
        if rec is None:
            return None
        if rec.status in PENDING_STATUSES:
            rec.status = "succeeded"
            rec.paid_at = rec.paid_at or self._now()
            if amount is not None:
                rec.amount = Decimal(str(amount))
            if charge_id:
                rec.telegram_charge_id = charge_id
            rec.meta.update(meta_patch or {})
        return self._copy(rec)

    async def set_status(self, payment_id, from_statuses: Iterable[str], to_status, meta_patch=None) -> bool:
        rec = self.payments.get(int(payment_id))
        if rec is None or rec.status not in set(from_statuses):
            return False
        rec.status = to_status
        rec.meta.update(meta_patch or {})
        return True

    async def patch_meta(self, payment_id, patch: Mapping[str, Any], *, claim=None, drop: Sequence[str] = ()) -> bool:
        rec = self.payments.get(int(payment_id))
        if rec is None:
            return False
        if claim is not None and rec.meta.get(claim):
            return False
        rec.meta.update(patch)
        if claim is not None:
            rec.meta[claim] = True
        for k in drop:
            rec.meta.pop(k, None)
        return True

    async def mark_fulfilled(self, payment_id, *, meta_patch=None) -> None:
        rec = self.payments[int(payment_id)]
        rec.meta[M_FULFILLED_AT] = rec.meta.get(M_FULFILLED_AT) or self._now().isoformat()
        rec.meta.update(meta_patch or {})
        for k in (M_NEEDS_PROVISIONING, "provisioning_error", "provisioning_attempted_at"):
            rec.meta.pop(k, None)
        sub = self.subs.get(rec.telegram_id)
        if sub and rec.kind in ("subscription", "autorenew") and rec.subscription_id is None:
            rec.subscription_id = sub.id

    async def payer_stats(self, telegram_id):
        paid = [r for r in self.payments.values()
                if r.telegram_id == telegram_id and r.status == "succeeded" and r.currency == "RUB"]
        return len(paid), sum((r.amount for r in paid), Decimal("0"))

    async def user_names(self, telegram_id):
        return self.users.get(telegram_id, ("", "", ""))

    async def recovery_candidates(self, now, *, pending_age, stuck_age, horizon, limit):
        now = now.replace(tzinfo=None) if now.tzinfo else now
        pending = [self._copy(r) for r in self.payments.values()
                   if r.status in PENDING_STATUSES and r.provider == "yookassa"
                   and now - horizon < r.created_at < now - pending_age][:limit]
        stuck = []
        for r in self.payments.values():
            if r.status != "succeeded" or r.provider not in ("yookassa", "stars") or r.subscription_id is not None:
                continue
            if r.fulfilled or r.meta.get(M_REVIEW_REJECTED) or (r.meta.get(M_NEEDS_REVIEW)
                                                               and not r.meta.get(M_REVIEW_APPROVED)):
                continue
            since = r.paid_at or r.created_at
            if r.meta.get(M_NEEDS_PROVISIONING) or since < now - stuck_age:
                stuck.append(self._copy(r))
        return pending, stuck[:limit]

    async def create_refund_request(self, payment_id, telegram_id, *, amount, reason):
        for rr in self.requests.values():
            if rr.payment_id == payment_id:
                return replace(rr), False
        rid = next(self._rid)
        rr = RefundRequestRecord(id=rid, payment_id=payment_id, telegram_id=telegram_id, status="pending",
                                 reason=reason, amount=amount, created_at=self._now())
        self.requests[rid] = rr
        return replace(rr), True

    async def get_refund_request(self, request_id):
        rr = self.requests.get(int(request_id))
        return replace(rr) if rr else None

    async def refund_request_for_payment(self, payment_id):
        found = [rr for rr in self.requests.values() if rr.payment_id == payment_id]
        return replace(found[-1]) if found else None

    async def transition_refund_request(self, request_id, from_statuses, to_status, *, decided_by=None):
        rr = self.requests.get(int(request_id))
        if rr is None or rr.status not in set(from_statuses):
            return None
        rr.status = to_status
        if decided_by is not None:
            rr.decided_by = decided_by
            rr.decided_at = self._now()
        return replace(rr)

    async def save_method(self, telegram_id, *, provider, external_id, title, card_fingerprint) -> int:
        for m in self.methods.values():
            if m.provider == provider and m.external_id == external_id:
                m.is_active = True
                return m.id
        mid = next(self._mid)
        self.methods[mid] = SavedMethodRecord(id=mid, telegram_id=telegram_id, provider=provider,
                                              external_id=external_id, title=title)
        return mid

    async def get_method(self, method_id):
        m = self.methods.get(int(method_id))
        return replace(m) if m else None

    async def main_subscription(self, telegram_id):
        s = self.subs.get(int(telegram_id))
        return replace(s) if s else None

    async def set_autorenew(self, telegram_id, enabled, *, method_id=None) -> bool:
        s = self.subs.get(int(telegram_id))
        if s is None:
            return False
        s.autorenew = bool(enabled)
        if enabled and method_id is not None:
            s.autorenew_method_id = method_id
        return True

    async def autorenew_subscriptions(self, until):
        until = until.replace(tzinfo=None) if until.tzinfo else until
        return [replace(s) for s in self.subs.values()
                if s.autorenew and s.active and not s.is_lifetime and s.valid_until and s.valid_until <= until]

    async def autorenew_attempts(self, telegram_id, period_key):
        return [self._copy(r) for r in sorted(self.payments.values(), key=lambda r: r.id)
                if r.telegram_id == telegram_id and r.kind == "autorenew" and r.meta.get("period_key") == period_key]

    async def last_paid_subscription(self, telegram_id):
        found = [r for r in self.payments.values() if r.telegram_id == telegram_id and r.status == "succeeded"
                 and r.kind in ("subscription", "autorenew") and r.plan_code and r.period_months]
        return self._copy(max(found, key=lambda r: r.id)) if found else None


class FakeProvisioning:
    """ProvisioningService: records grants, idempotent per payment_id; can fail."""

    def __init__(self, store: Optional[InMemoryPaymentStore] = None, clock: Optional[Clock] = None):
        self.store = store
        self.clock = clock or Clock()
        self.grants: list[tuple[int, Entitlement]] = []
        self.revokes: list[tuple[int, str]] = []
        self.by_payment: dict[int, SubscriptionState] = {}
        self.fail_times = 0
        self.fail_with: Exception = RuntimeError("panel down")
        self.revoke_result = True

    async def grant(self, telegram_id: int, entitlement: Entitlement, *, trace_id: str) -> SubscriptionState:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.fail_with
        if entitlement.payment_id in self.by_payment:
            return self.by_payment[entitlement.payment_id]
        self.grants.append((telegram_id, entitlement))
        base = self.clock()
        if self.store is not None:
            sub = self.store.subs.get(telegram_id)
            if sub and sub.valid_until and sub.valid_until.replace(tzinfo=timezone.utc) > base:
                base = sub.valid_until.replace(tzinfo=timezone.utc)
        until = base + timedelta(days=entitlement.days or 0)
        if self.store is not None:
            sub = self.store.subs.get(telegram_id) or self.store.add_sub(telegram_id)
            sub.valid_until, sub.active, sub.plan_code = until.replace(tzinfo=None), True, entitlement.plan_code
        state = SubscriptionState(telegram_id=telegram_id, active=True, plan_code=entitlement.plan_code,
                                  expires_at=until)
        self.by_payment[entitlement.payment_id] = state
        return state

    async def revoke(self, telegram_id: int, *, sub_kind: SubKind = SubKind.MAIN, reason: str, trace_id: str) -> bool:
        self.revokes.append((telegram_id, reason))
        return self.revoke_result


class FakePromo:
    def __init__(self, with_gifts: bool = True):
        self.gifts: list[tuple[int, str, int, int]] = []
        if not with_gifts:
            self.create_gift = None  # type: ignore[assignment]

    async def create_gift(self, buyer_telegram_id: int, plan_code: str, months: int, *, payment_id: int) -> str:
        for g in self.gifts:
            if g[3] == payment_id:
                return f"GIFT{payment_id}"
        self.gifts.append((buyer_telegram_id, plan_code, months, payment_id))
        return f"GIFT{payment_id}"


class FakeHooks:
    def __init__(self):
        self.obhod_ok = True
        self.obhod_calls: list[tuple] = []
        self.after_paid_calls: list[int] = []
        self.blocked_users: dict[int, str] = {}
        self.blocked_cards: dict[str, str] = {}
        self.last_plans: dict[int, str] = {}

    async def apply_obhod_package(self, telegram_id, package_code, payment_id, trace_id) -> bool:
        self.obhod_calls.append((telegram_id, package_code, payment_id))
        return self.obhod_ok

    async def after_paid(self, payment_id, bot) -> None:
        self.after_paid_calls.append(payment_id)

    async def invalidate_caches(self, telegram_id) -> None:
        return None

    async def user_block_reason(self, telegram_id):
        return self.blocked_users.get(telegram_id)

    async def card_block_reason(self, fingerprint):
        return self.blocked_cards.get(fingerprint)

    async def last_plan(self, telegram_id):
        return self.last_plans.get(telegram_id)

    async def suppress_expiry_notices(self, telegram_id, expires_at) -> None:
        return None


class Settings:
    """Minimal settings object for money tests."""

    def __init__(self, **kw):
        self.AUTOPAY_ENABLED = False
        self.STARS_ENABLED = False
        self.STARS_RATE = 0.0
        self.GIFTS_ENABLED = False
        self.REFUND_24H_ENABLED = False
        self.ADMINS = [111]
        self.SUPPORT_HANDLE = "crs_support"
        self.ADMIN_SUPPORT_USERNAME = None
        for k, v in kw.items():
            setattr(self, k, v)


def make_money(**overrides):
    """(MoneyServices, parts) built from fakes. overrides: settings kwargs + any part."""
    from app.services.money import MoneyDeps, build_money
    from app.services.payments.ui import NullUi
    from tests.fakes.notifier import RecordingNotifier
    from tests.fakes.payments import FakePaymentGateway
    from tests.fakes.stars import FakeStarsGateway

    clock = overrides.pop("clock", None) or Clock()
    store = overrides.pop("store", None) or InMemoryPaymentStore(clock)
    parts = {
        "payments": overrides.pop("payments", None) or FakePaymentGateway(),
        "stars": overrides.pop("stars", None) or FakeStarsGateway(),
        "provisioning": overrides.pop("provisioning", None) or FakeProvisioning(store, clock),
        "notifier": overrides.pop("notifier", None) or RecordingNotifier(),
        "promo": overrides.pop("promo", None) or FakePromo(),
        "store": store,
        "ui": overrides.pop("ui", None) or NullUi(),
        "hooks": overrides.pop("hooks", None) or FakeHooks(),
        "bot": overrides.pop("bot", None),
        "clock": clock,
    }
    settings = overrides.pop("settings", None) or Settings(**overrides)
    deps = MoneyDeps(settings=settings, **parts)
    return build_money(deps), deps
