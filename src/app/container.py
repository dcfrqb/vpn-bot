"""Composition root of release 3.0. FROZEN seam.

The ONLY place that decides which implementation backs each port
(app.services.ports). Bot (app.main), webhook API (app.api.app) and the
worker (app.worker.scheduler) build one Container at startup and pass it
down; nothing else instantiates services.

Since the 3.0 cutover every port is backed by its real implementation
(infra/* adapters, services/* over them). A new implementation changes one
line in ``build_container`` and keeps the port signature.

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
    """Wire every port. ``overrides`` replace single ports (tests, debug);
    the services that depend on an overridden port are built over it."""
    from app.infra.remnawave.gateway import HttpRemnaGateway
    from app.infra.telegram_stars import TelegramStarsGateway
    from app.infra.yookassa.gateway import YooKassaGateway
    from app.services.checkout import ContainerCheckout
    from app.services.devices import PanelDevicesService
    from app.services.maintenance import RedisMaintenanceGuard
    from app.services.notifications import TelegramNotifier
    from app.services.promo import PromoEngine
    from app.services.provisioning import PanelProvisioningService
    from app.services.status import PanelStatusService

    if settings is None:
        from app.config import settings as _settings

        settings = _settings

    unknown = set(overrides) - ({f.name for f in fields(Container)} - {"bot", "settings"})
    if unknown:
        raise TypeError(f"unknown container overrides: {sorted(unknown)}")

    def pick(name: str, factory: Any) -> Any:
        value = overrides.get(name)
        return value if value is not None else factory()

    # One gateway per process: the 10-minute squad cache is shared.
    remna = pick("remna", HttpRemnaGateway)
    notifier = pick("notifier", lambda: TelegramNotifier(bot, settings))
    status = pick("status", lambda: PanelStatusService(remna))
    provisioning = pick("provisioning", lambda: PanelProvisioningService(remna, notifier=notifier, status=status))
    parts: dict[str, Any] = {
        "remna": remna,
        "payments": pick("payments", YooKassaGateway),
        "stars": pick("stars", lambda: TelegramStarsGateway(bot)),
        "provisioning": provisioning,
        "status": status,
        "devices": pick("devices", lambda: PanelDevicesService(remna, status=status)),
        "checkout": pick("checkout", ContainerCheckout),
        "promo": pick("promo", lambda: PromoEngine(provisioning=provisioning, status=status,
                                                     notifier=notifier, settings=settings)),
        "notifier": notifier,
        "maintenance": pick("maintenance", RedisMaintenanceGuard),
    }
    container = Container(bot=bot, settings=settings, **parts)
    if isinstance(container.checkout, ContainerCheckout):
        container.checkout.bind(container)
    return container


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
