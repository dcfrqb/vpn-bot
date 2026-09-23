"""Service ports (release 3.0 Foundation). FROZEN seam.

Protocols that bot routers, API routes and worker jobs depend on. Concrete
implementations are wired in ONE place, app.container. Foundation wires the
shims from app.services.shims (ports over 2.1.1 code); streams replace them
with real implementations without changing these signatures.

Rules:
- No aiogram / FastAPI imports here or in any implementation under
  app/services. Anything Telegram-shaped (reply markup) is passed as ``Any``.
- All methods are async and never raise for "expected" outcomes (not found,
  not eligible, busy): they return a DTO/None/False. They raise only for
  infrastructure failures the caller cannot handle meaningfully.
- Prices come only from app.domain.plans (via CheckoutService.quote).
- RemnaGateway never writes manual squads (*-m, *-friend, arcadia) and never
  lowers hwidDeviceLimit; tests/invariants enforce that on every adapter.

Changing a signature = orchestrator commit on release/3.0 + rebase of all
streams. Adding a new optional keyword argument with a default is allowed for
the owning stream (note it in its impl report).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncIterator, Mapping, Optional, Protocol, Sequence, runtime_checkable

from app.domain.models import (
    AdminTopic,
    DeviceInfo,
    Entitlement,
    PanelUser,
    PaymentIntent,
    PromoReward,
    Quote,
    SubKind,
    SubscriptionState,
)

__all__ = [
    "RemnaGateway",
    "PaymentGateway",
    "StarsGateway",
    "ProvisioningService",
    "StatusService",
    "DevicesService",
    "CheckoutService",
    "PromoService",
    "Notifier",
    "MaintenanceGuard",
]


# ---------------------------------------------------------------------------
# Gateways (infra adapters)
# ---------------------------------------------------------------------------


@runtime_checkable
class RemnaGateway(Protocol):
    """Remnawave panel. Owner: stream B. Implemented over RemnaClient by
    shims.LegacyRemnaGateway in Foundation."""

    async def get_user(self, panel_id: int) -> Optional[PanelUser]:
        """User by numeric panel id; None if the panel says 404."""

    async def find_users_by_telegram_id(self, telegram_id: int) -> list[PanelUser]:
        """All panel users bound to this Telegram id (main + obhod + strays)."""

    async def get_subscription_url(self, panel_id: int) -> Optional[str]:
        """Subscription URL (domain override applied). NEVER log the result."""

    async def create_user(
        self,
        username: str,
        *,
        telegram_id: Optional[int],
        expire_at: datetime,
        squads: Sequence[str] = (),
        device_limit: Optional[int] = None,
        traffic_limit_bytes: Optional[int] = None,
        traffic_limit_strategy: Optional[str] = None,
    ) -> PanelUser:
        """Create a panel user. ``squads`` are squad NAMES (resolved inside)."""

    async def update_user(
        self,
        panel_id: int,
        *,
        expire_at: Optional[datetime] = None,
        squads: Optional[Sequence[str]] = None,
        device_limit: Optional[int] = None,
        traffic_limit_bytes: Optional[int] = None,
        traffic_limit_strategy: Optional[str] = None,
    ) -> PanelUser:
        """PATCH only the given fields. ``squads`` = full target list of NAMES;
        manual squads already on the user are preserved by the adapter."""

    async def enable_user(self, panel_id: int) -> None: ...

    async def disable_user(self, panel_id: int) -> None: ...

    async def list_squads(self) -> Mapping[str, str]:
        """Internal squads: name -> uuid."""

    async def list_devices(self, panel_id: int) -> list[DeviceInfo]: ...

    async def delete_device(self, panel_id: int, hwid: str) -> bool: ...

    def iter_users(self, page_size: int = 500) -> AsyncIterator[PanelUser]:
        """Async iterator over ALL panel users (paginated inside)."""

    async def ping(self) -> bool:
        """Cheap health probe (panel API answers)."""


@runtime_checkable
class PaymentGateway(Protocol):
    """YooKassa. Owner: stream A."""

    async def create_payment(
        self,
        intent: PaymentIntent,
        *,
        description: str,
        idempotence_key: str,
        save_payment_method: bool = False,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> PaymentIntent:
        """Create a payment; returns the intent with external_id and
        confirmation_url filled. Amount is taken from ``intent`` which must be
        a server-side Quote price."""

    async def get_payment(self, external_id: str) -> Optional[dict]:
        """Provider view of the payment (status, paid, amount, metadata,
        refunded_amount, payment_method) or None on API/network error.
        {"error": "not_found"} when the provider does not know the id."""

    async def charge_saved_method(
        self,
        intent: PaymentIntent,
        *,
        payment_method_id: str,
        description: str,
        idempotence_key: str,
    ) -> PaymentIntent:
        """Autopay: charge a saved card without user interaction."""

    async def refund(
        self,
        external_id: str,
        *,
        amount_rub: Optional[Decimal] = None,
        idempotence_key: str,
        reason: str = "",
    ) -> Optional[dict]:
        """Create a refund (full when amount_rub is None). None on failure."""


@runtime_checkable
class StarsGateway(Protocol):
    """Telegram Stars (XTR). Owner: stream A."""

    async def send_invoice(self, chat_id: int, intent: PaymentIntent, *, title: str, description: str, payload: str) -> None: ...

    async def refund(self, telegram_id: int, telegram_charge_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# Application services
# ---------------------------------------------------------------------------


@runtime_checkable
class ProvisioningService(Protocol):
    """Grants and revokes access in the panel + DB. Owner: stream B.
    The ONLY place that creates panel accounts (trial/payment/promo/admin)."""

    async def grant(self, telegram_id: int, entitlement: Entitlement, *, trace_id: str) -> SubscriptionState:
        """Apply an entitlement (extend from max(now, current expiry)).
        Idempotent per (entitlement.payment_id or trace_id)."""

    async def revoke(self, telegram_id: int, *, sub_kind: SubKind = SubKind.MAIN, reason: str, trace_id: str) -> bool:
        """Cut access (refund). Never shortens a lifetime/manual subscription."""


@runtime_checkable
class StatusService(Protocol):
    """Status card data. Owner: stream B."""

    async def get_state(self, telegram_id: int, *, force: bool = False) -> SubscriptionState:
        """Cached (short TTL) unless ``force``; panel down -> stale=True."""

    async def invalidate(self, telegram_id: int) -> None: ...


@runtime_checkable
class DevicesService(Protocol):
    """HWID devices of the user's main panel account. Owner: stream B."""

    async def list_devices(self, telegram_id: int) -> list[DeviceInfo]: ...

    async def unlink(self, telegram_id: int, device_short_id: str) -> bool:
        """Unlink by DeviceInfo.short_id; False when rate-limited (3 per 24h)
        or not found."""

    async def unlinks_left(self, telegram_id: int) -> int: ...


@runtime_checkable
class CheckoutService(Protocol):
    """Plan -> period -> payment. Owner: stream A."""

    async def quote(self, telegram_id: int, plan_code: str, months: int) -> Optional[Quote]:
        """Server price for this user; None when not sellable to them."""

    async def start(
        self,
        telegram_id: int,
        quote: Quote,
        *,
        method: str = "yookassa",
        autorenew: bool = False,
    ) -> PaymentIntent:
        """Create or reuse (15 min) a pending payment for this quote."""

    async def check(self, telegram_id: int, payment_id: int) -> PaymentIntent:
        """Re-check a payment with the provider and fulfil it if paid."""


@runtime_checkable
class PromoService(Protocol):
    """Promo codes, trial, gifts. Owner: stream E."""

    async def redeem(self, telegram_id: int, code: str, *, source: str = "command") -> PromoReward: ...

    async def start_trial(self, telegram_id: int) -> PromoReward: ...

    async def trial_available(self, telegram_id: int) -> bool: ...


@runtime_checkable
class Notifier(Protocol):
    """Admin and user notifications. Implemented in Foundation
    (app.services.notifications.TelegramNotifier)."""

    async def notify_admins(
        self,
        topic: AdminTopic,
        text: str,
        *,
        html: bool = False,
        dedup_key: Optional[str] = None,
        dedup_ttl: int = 3600,
        reply_markup: Any = None,
        disable_notification: bool = False,
    ) -> int:
        """Send to the admin forum topic, or DM every admin as a fallback.
        ``html=False`` escapes ``text``; ``html=True`` means the caller built
        safe HTML with app.domain.texts.h(). Returns delivered message count
        (0 when deduplicated or nobody reachable)."""

    async def notify_user(
        self,
        telegram_id: int,
        text: str,
        *,
        html: bool = False,
        reply_markup: Any = None,
        dedup_key: Optional[str] = None,
        dedup_ttl: int = 3600,
    ) -> bool: ...


@runtime_checkable
class MaintenanceGuard(Protocol):
    """Maintenance mode switch. Foundation: manual Redis flag only.
    Stream C adds the automatic panel health probe."""

    async def is_active(self) -> bool: ...

    async def reason(self) -> Optional[str]: ...

    async def set_active(self, active: bool, *, reason: str = "", by: Optional[int] = None) -> None: ...
