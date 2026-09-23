"""Shared domain DTOs (release 3.0 Foundation). FROZEN seam.

Every stream codes against these types. Changes only through an orchestrator
commit on release/3.0 (see docs/ARCHITECTURE_3.0.md, "Frozen files").

Rules:
- Plain frozen dataclasses and str-Enums: no aiogram, no SQLAlchemy, no I/O.
- Money is integer rubles (``amount_rub``); kopecks never appear in 3.0 plans.
- Datetimes are timezone-aware UTC. Naive values from the DB are converted by
  the caller (``ensure_utc``).
- Subscription URLs and other secrets are ``repr=False`` so they never leak
  into logs through an f-string of the object.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Naive datetime (DB stores naive UTC) -> aware UTC. None stays None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SubKind(str, enum.Enum):
    """subscriptions.sub_kind. Keep ``sub_kind == "main"`` filters everywhere."""

    MAIN = "main"
    OBHOD = "obhod"


class AdminTopic(str, enum.Enum):
    """Forum topics of the admin supergroup (ADMIN_CHAT_ID + ADMIN_TOPIC_*).

    GENERAL has no topic setting: it goes to the chat root (or DM fallback).
    """

    PAYMENTS = "payments"
    REFUNDS = "refunds"
    PANEL = "panel"
    ERRORS = "errors"
    PROMO = "promo"
    BROADCAST = "broadcast"
    GENERAL = "general"


class PaymentKind(str, enum.Enum):
    """payments.kind (column added by r30_01, NULL on old rows)."""

    SUBSCRIPTION = "subscription"
    OBHOD_PACKAGE = "obhod_package"
    GIFT = "gift"
    AUTORENEW = "autorenew"
    PROMO = "promo"


class PaymentMethod(str, enum.Enum):
    """payments.method (column added by r30_01, NULL on old rows)."""

    YOOKASSA = "yookassa"
    STARS = "stars"
    SAVED_CARD = "saved_card"
    MANUAL = "manual"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"
    FAILED = "failed"
    REFUNDED = "refunded"


class EntitlementSource(str, enum.Enum):
    PAYMENT = "payment"
    AUTORENEW = "autorenew"
    TRIAL = "trial"
    PROMO = "promo"
    GIFT = "gift"
    ADMIN = "admin"


@dataclass(frozen=True)
class Quote:
    """Server-side price of one purchase. Built only from domain.plans."""

    plan_code: str
    months: int
    amount_rub: int
    title: str = ""
    currency: str = "RUB"
    stars: Optional[int] = None  # XTR price when STARS_ENABLED, else None
    is_legacy: bool = False  # basic/premium renewal for its owner

    @property
    def sellable(self) -> bool:
        return self.amount_rub > 0


@dataclass(frozen=True)
class Entitlement:
    """What the user is entitled to after a payment/promo/trial/admin grant.

    ``months`` (calendar months, payments) or ``days`` extend from
    max(now, current expiry); ``until`` sets an exact end. Exactly one of them
    is set (``is_lifetime`` needs none). ``squad``/``device_limit`` default to
    the plan catalog when None. ``clear_grace``: a grant ends stream C's
    grace period (restores the plan squad and the traffic cap).
    """

    plan_code: str
    source: EntitlementSource
    sub_kind: SubKind = SubKind.MAIN
    days: Optional[int] = None
    until: Optional[datetime] = None
    is_lifetime: bool = False
    squad: Optional[str] = None
    device_limit: Optional[int] = None
    traffic_limit_bytes: Optional[int] = None
    payment_id: Optional[int] = None
    note: str = ""
    months: Optional[int] = None
    clear_grace: bool = True


@dataclass(frozen=True)
class SubscriptionState:
    """What the user sees in the menu. Built by StatusService (panel + DB)."""

    telegram_id: int
    has_panel_user: bool = False
    active: bool = False
    plan_code: Optional[str] = None
    sub_kind: SubKind = SubKind.MAIN
    expires_at: Optional[datetime] = None
    is_lifetime: bool = False
    device_limit: Optional[int] = None
    devices_used: Optional[int] = None
    panel_status: Optional[str] = None  # ACTIVE / DISABLED / EXPIRED / LIMITED
    autorenew: bool = False
    grace_until: Optional[datetime] = None
    is_trial: bool = False
    obhod_active: bool = False
    obhod_used_bytes: Optional[int] = None
    obhod_limit_bytes: Optional[int] = None
    subscription_url: Optional[str] = field(default=None, repr=False)
    obhod_subscription_url: Optional[str] = field(default=None, repr=False)
    fetched_at: Optional[datetime] = None
    stale: bool = False  # True when the panel was unreachable and data is cached/DB-only

    def days_left(self, now: Optional[datetime] = None) -> Optional[int]:
        """Whole days left (ceil), 0 when expired, None when lifetime/unknown."""
        if self.is_lifetime or self.expires_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        seconds = (ensure_utc(self.expires_at) - now).total_seconds()
        if seconds <= 0:
            return 0
        return int(-(-seconds // 86400))


@dataclass(frozen=True)
class DeviceInfo:
    """One HWID device of a panel user."""

    hwid: str = field(repr=False)
    platform: Optional[str] = None
    os_version: Optional[str] = None
    device_model: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def short_id(self) -> str:
        """Non-secret handle for callbacks/logs (last 8 chars of hwid)."""
        return self.hwid[-8:]


@dataclass(frozen=True)
class PanelUser:
    """Remnawave user as the bot needs it (DTO over the panel JSON)."""

    id: int
    uuid: str = ""
    username: str = ""
    telegram_id: Optional[int] = None
    status: Optional[str] = None
    expire_at: Optional[datetime] = None
    squads: tuple[str, ...] = ()  # squad NAMES
    squad_uuids: tuple[str, ...] = ()
    device_limit: Optional[int] = None
    traffic_limit_bytes: Optional[int] = None
    traffic_limit_strategy: Optional[str] = None
    used_traffic_bytes: Optional[int] = None
    subscription_url: Optional[str] = field(default=None, repr=False)
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)


class PromoOutcome(str, enum.Enum):
    APPLIED = "applied"
    NOT_FOUND = "not_found"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"
    ALREADY_USED = "already_used"
    NOT_ELIGIBLE = "not_eligible"
    BUSY = "busy"  # lock held by a parallel redemption
    DISABLED = "disabled"
    RATE_LIMITED = "rate_limited"  # too many unknown codes in a row (security m-1)
    ERROR = "error"


@dataclass(frozen=True)
class PromoReward:
    """Result of a promo/trial/gift redemption."""

    code: str
    outcome: PromoOutcome
    plan_code: Optional[str] = None
    days: Optional[int] = None
    months: Optional[int] = None
    expires_at: Optional[datetime] = None
    discount_percent: Optional[int] = None
    redemption_id: Optional[int] = None

    @property
    def applied(self) -> bool:
        return self.outcome is PromoOutcome.APPLIED


@dataclass(frozen=True)
class PaymentIntent:
    """A payment the user is about to make / has made (one payments row)."""

    plan_code: str
    months: int
    amount_rub: int
    kind: PaymentKind = PaymentKind.SUBSCRIPTION
    method: PaymentMethod = PaymentMethod.YOOKASSA
    telegram_id: Optional[int] = None
    payment_id: Optional[int] = None  # payments.id
    external_id: Optional[str] = None  # provider id (YooKassa uuid / Stars charge id)
    status: PaymentStatus = PaymentStatus.PENDING
    autorenew: bool = False
    stars: Optional[int] = None
    confirmation_url: Optional[str] = field(default=None, repr=False)
    created_at: Optional[datetime] = None
