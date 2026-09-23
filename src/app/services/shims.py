"""Ports implemented over 2.1.1 code (release 3.0 Foundation).

These adapters let new routers/jobs code against app.services.ports today.
Each owning stream replaces its shim with a real implementation and rewires
app.container; the shim is deleted at cutover.

What is real here and what is a placeholder:

| Port                | Shim                       | State                                   |
|---------------------|----------------------------|-----------------------------------------|
| RemnaGateway        | LegacyRemnaGateway         | = infra.remnawave.gateway.HttpRemnaGateway (B) |
| PaymentGateway      | LegacyPaymentGateway       | = infra.yookassa.YooKassaGateway (A)    |
| StarsGateway        | DisabledStarsGateway       | placeholder; money swaps in the real one|
| ProvisioningService | LegacyProvisioningService  | = services.provisioning.PanelProvisioningService (B) |
| StatusService       | LegacyStatusService        | = services.status.PanelStatusService (B) |
| DevicesService      | UnavailableDevicesService  | = services.devices.PanelDevicesService (B) |
| CheckoutService     | LegacyCheckoutService      | -> services.checkout.CheckoutServiceImpl (A) |
| PromoService        | LegacyPromoService         | placeholder -> E                        |
| MaintenanceGuard    | RedisMaintenanceGuard      | real (manual Redis flag)                |
| Notifier            | notifications.TelegramNotifier (not a shim)                          |

Stream B: its four names are now aliases of the real services, so
app.container (frozen) wires them without an edit; the orchestrator renames
the lines at cutover (impl/requests/B.md).

Placeholders raise NotImplementedError("<port>: owned by stream X"), so a
premature call fails loudly in tests instead of silently doing nothing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from app.domain.models import (
    PanelUser,
    PaymentIntent,
    PromoReward,
    Quote,
)
from app.infra.yookassa.gateway import YooKassaGateway
from app.logger import logger


def _placeholder(port: str, stream: str) -> NotImplementedError:
    return NotImplementedError(f"{port}: not in Foundation shim, owned by stream {stream}")


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _unwrap(data: Any) -> dict:
    raw = data.get("response", data) if isinstance(data, dict) else {}
    return raw if isinstance(raw, dict) else {}


def panel_user_from_raw(raw: Mapping[str, Any], uuid_to_name: Optional[Mapping[str, str]] = None) -> PanelUser:
    """Remnawave 3.4.3 user JSON -> PanelUser."""
    squads = raw.get("activeInternalSquads") or []
    uuids: list[str] = []
    names: list[str] = []
    for item in squads:
        if isinstance(item, dict):
            u, n = item.get("uuid"), item.get("name")
        else:
            u, n = item, None
        if u:
            uuids.append(str(u))
        n = n or (uuid_to_name or {}).get(str(u))
        if n:
            names.append(str(n))
    traffic = raw.get("userTraffic") if isinstance(raw.get("userTraffic"), dict) else {}
    used = raw.get("usedTrafficBytes", traffic.get("usedTrafficBytes"))
    tg = raw.get("telegramId")
    return PanelUser(
        id=int(raw.get("id") or 0),
        uuid=str(raw.get("uuid") or ""),
        username=str(raw.get("username") or ""),
        telegram_id=int(tg) if tg not in (None, "") else None,
        status=raw.get("status"),
        expire_at=_parse_dt(raw.get("expireAt")),
        squads=tuple(names),
        squad_uuids=tuple(uuids),
        device_limit=raw.get("hwidDeviceLimit"),
        traffic_limit_bytes=raw.get("trafficLimitBytes"),
        traffic_limit_strategy=raw.get("trafficLimitStrategy"),
        used_traffic_bytes=int(used) if used not in (None, "") else None,
        subscription_url=raw.get("subscriptionUrl"),
        raw=dict(raw),
    )


# Stream B: real adapter (manual squads kept, limit never lowered, devices, paging from 0).
from app.infra.remnawave.gateway import HttpRemnaGateway as LegacyRemnaGateway  # noqa: E402,F401 (re-export for app.container)


class LegacyPaymentGateway(YooKassaGateway):
    """PaymentGateway: since stream A the async YooKassa gateway
    (app.infra.yookassa.YooKassaGateway, no SDK, no database). Kept under this
    name so the frozen container keeps wiring it; the orchestrator renames the
    container line at the cutover."""


class DisabledStarsGateway:
    async def send_invoice(self, chat_id, intent, *, title, description, payload) -> None:
        raise _placeholder("StarsGateway.send_invoice", "A")

    async def refund(self, telegram_id: int, telegram_charge_id: str) -> bool:
        raise _placeholder("StarsGateway.refund", "A")


# Stream B: real services (panel account created only by provisioning).
from app.services.devices import PanelDevicesService as UnavailableDevicesService  # noqa: E402,F401 (re-export for app.container)
from app.services.provisioning import PanelProvisioningService as LegacyProvisioningService  # noqa: E402,F401 (re-export for app.container)
from app.services.status import PanelStatusService as LegacyStatusService  # noqa: E402,F401 (re-export for app.container)


class LegacyCheckoutService:
    """CheckoutService: delegates to app.services.checkout.CheckoutServiceImpl
    (stream A) built over the process container by app.services.money."""

    def __init__(self, payments: Any = None):
        self._payments = payments if payments is not None else LegacyPaymentGateway()

    def _impl(self):
        from app.services.money import money

        try:
            from app.container import get_container

            container = get_container()
        except RuntimeError:
            container = None
        if container is not None:
            return money(container).checkout
        return _standalone_checkout(self._payments)

    async def quote(self, telegram_id: int, plan_code: str, months: int) -> Optional[Quote]:
        return await self._impl().quote(telegram_id, plan_code, months)

    async def start(self, telegram_id: int, quote: Quote, *, method: str = "yookassa", autorenew: bool = False) -> PaymentIntent:
        return await self._impl().start(telegram_id, quote, method=method, autorenew=autorenew)

    async def check(self, telegram_id: int, payment_id: int) -> PaymentIntent:
        return await self._impl().check(telegram_id, payment_id)


def _standalone_checkout(payments: Any):
    """Checkout without a process container (scripts, unit tests of quote)."""
    from app.config import settings
    from app.infra.telegram_stars import TelegramStarsGateway
    from app.services.money import LegacyHooks, MoneyDeps, build_money
    from app.services.payments.store import SqlPaymentStore
    from app.services.payments.ui import NullUi

    deps = MoneyDeps(payments=payments, stars=TelegramStarsGateway(), provisioning=LegacyProvisioningService(),
                     notifier=None, promo=LegacyPromoService(), store=SqlPaymentStore(), settings=settings,
                     ui=NullUi(), hooks=LegacyHooks())
    return build_money(deps).checkout


class LegacyPromoService:
    async def redeem(self, telegram_id: int, code: str, *, source: str = "command") -> PromoReward:
        raise _placeholder("PromoService.redeem", "E")

    async def start_trial(self, telegram_id: int) -> PromoReward:
        raise _placeholder("PromoService.start_trial", "E")

    async def trial_available(self, telegram_id: int) -> bool:
        raise _placeholder("PromoService.trial_available", "E")


MAINTENANCE_KEY = "maintenance:state"


class RedisMaintenanceGuard:
    """Manual maintenance switch stored in Redis (``maintenance:state`` JSON).

    Redis unavailable -> not active (the bot keeps serving, as in 2.x).
    """

    async def _state(self) -> Optional[dict]:
        from app.infra.redis.flags import get_value

        raw = await get_value(MAINTENANCE_KEY)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            data = {"reason": ""}
        return data if isinstance(data, dict) else {"reason": ""}

    async def is_active(self) -> bool:
        return (await self._state()) is not None

    async def reason(self) -> Optional[str]:
        state = await self._state()
        return (state or {}).get("reason") if state else None

    async def set_active(self, active: bool, *, reason: str = "", by: Optional[int] = None) -> None:
        from app.infra.redis.flags import delete_key, set_value

        if active:
            payload = {"reason": reason, "by": by, "since": datetime.now(timezone.utc).isoformat()}
            await set_value(MAINTENANCE_KEY, json.dumps(payload, ensure_ascii=False))
            logger.warning(f"maintenance ON by={by} reason={reason!r}")
        else:
            await delete_key(MAINTENANCE_KEY)
            logger.warning(f"maintenance OFF by={by}")
