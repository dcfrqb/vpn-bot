"""Composition root of release 3.0. FROZEN seam.

The ONLY place that decides which implementation backs each port
(app.services.ports). Bot (app.main), webhook API (app.api.app) and the
worker (app.worker.scheduler) build one Container at startup and pass it
down; nothing else instantiates services.

Foundation wires the 2.1.1 shims (app.services.shims). A stream that ships
a real implementation changes exactly one line in ``build_container`` (via
an orchestrator commit) and keeps the port signature.

Tests build a container with fakes:

    c = build_container(bot, remna=FakeRemnaGateway(), notifier=RecordingNotifier())
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Optional

from app.services.ports import (
    CheckoutService,
    DevicesService,
    MaintenanceGuard,
    Notifier,
    PaymentGateway,
    PromoService,
    ProvisioningService,
    RemnaGateway,
    StarsGateway,
    StatusService,
)

# Handler-data key for each port (DIMiddleware). "status" is a common word,
# hence "status_service".
HANDLER_KEYS = {
    "remna": "remna",
    "payments": "payments",
    "stars": "stars",
    "provisioning": "provisioning",
    "status": "status_service",
    "devices": "devices",
    "checkout": "checkout",
    "promo": "promo",
    "notifier": "notifier",
    "maintenance": "maintenance",
}


@dataclass
class Container:
    bot: Any
    settings: Any
    remna: RemnaGateway
    payments: PaymentGateway
    stars: StarsGateway
    provisioning: ProvisioningService
    status: StatusService
    devices: DevicesService
    checkout: CheckoutService
    promo: PromoService
    notifier: Notifier
    maintenance: MaintenanceGuard

    def handler_data(self) -> dict[str, Any]:
        """Keys injected into aiogram handler data by DIMiddleware."""
        data: dict[str, Any] = {"container": self}
        for attr, key in HANDLER_KEYS.items():
            data[key] = getattr(self, attr)
        return data


_current: Optional[Container] = None


def build_container(bot: Any, *, settings: Any = None, **overrides: Any) -> Container:
    """Wire every port. ``overrides`` replace single ports (tests, debug)."""
    from app.services import shims
    from app.services.notifications import TelegramNotifier

    if settings is None:
        from app.config import settings as _settings

        settings = _settings

    unknown = set(overrides) - ({f.name for f in fields(Container)} - {"bot", "settings"})
    if unknown:
        raise TypeError(f"unknown container overrides: {sorted(unknown)}")

    payments = overrides.pop("payments", None) or shims.LegacyPaymentGateway()
    parts: dict[str, Any] = {
        "remna": shims.LegacyRemnaGateway(),
        "payments": payments,
        "stars": shims.DisabledStarsGateway(),
        "provisioning": shims.LegacyProvisioningService(),
        "status": shims.LegacyStatusService(),
        "devices": shims.UnavailableDevicesService(),
        "checkout": shims.LegacyCheckoutService(payments),
        "promo": shims.LegacyPromoService(),
        "notifier": TelegramNotifier(bot, settings),
        "maintenance": shims.RedisMaintenanceGuard(),
    }
    parts.update(overrides)
    return Container(bot=bot, settings=settings, **parts)


def set_container(container: Optional[Container]) -> None:
    global _current
    _current = container


def get_container() -> Container:
    """The process-wide container (set by app.main / app.api.app at startup).

    Prefer DI (handler data) in bot handlers; this is for jobs and API routes.
    """
    if _current is None:
        raise RuntimeError("container is not built yet (call build_container + set_container at startup)")
    return _current
