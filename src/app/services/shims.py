"""Ports implemented over 2.1.1 code (release 3.0 Foundation).

These adapters let new routers/jobs code against app.services.ports today.
Each owning stream replaces its shim with a real implementation and rewires
app.container; the shim is deleted at cutover.

What is real here and what is a placeholder:

| Port                | Shim                       | State                                   |
|---------------------|----------------------------|-----------------------------------------|
| RemnaGateway        | LegacyRemnaGateway         | = infra.remnawave.gateway.HttpRemnaGateway (B) |
| PaymentGateway      | LegacyPaymentGateway       | create/get real, charge/refund -> A     |
| StarsGateway        | DisabledStarsGateway       | placeholder -> A                        |
| ProvisioningService | LegacyProvisioningService  | = services.provisioning.PanelProvisioningService (B) |
| StatusService       | LegacyStatusService        | = services.status.PanelStatusService (B) |
| DevicesService      | UnavailableDevicesService  | = services.devices.PanelDevicesService (B) |
| CheckoutService     | LegacyCheckoutService      | quote/start real, check -> A            |
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
    PaymentStatus,
    PromoReward,
    Quote,
)
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


class LegacyPaymentGateway:
    """PaymentGateway over services.payments.yookassa (sync SDK in 2.x)."""

    async def create_payment(
        self,
        intent: PaymentIntent,
        *,
        description: str,
        idempotence_key: str,
        save_payment_method: bool = False,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> PaymentIntent:
        if save_payment_method:
            raise _placeholder("PaymentGateway.create_payment(save_payment_method)", "A")
        from dataclasses import replace

        from app.services.payments.yookassa import create_payment

        # 2.x create_payment prices server-side itself and raises on mismatch.
        url, external_id = await create_payment(
            amount_rub=intent.amount_rub,
            description=description,
            user_id=int(intent.telegram_id or 0),
            plan_code=intent.plan_code,
            period_months=intent.months,
        )
        return replace(intent, external_id=external_id, confirmation_url=url, status=PaymentStatus.PENDING)

    async def get_payment(self, external_id: str) -> Optional[dict]:
        from app.services.payments.yookassa import check_payment_status

        return await check_payment_status(external_id)

    async def charge_saved_method(self, intent, *, payment_method_id, description, idempotence_key) -> PaymentIntent:
        raise _placeholder("PaymentGateway.charge_saved_method", "A")

    async def refund(self, external_id, *, amount_rub=None, idempotence_key, reason="") -> Optional[dict]:
        raise _placeholder("PaymentGateway.refund", "A")


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
    """quote/start over services.checkout + yookassa.create_payment."""

    def __init__(self, payments: Optional[LegacyPaymentGateway] = None):
        self._payments = payments or LegacyPaymentGateway()

    async def quote(self, telegram_id: int, plan_code: str, months: int) -> Optional[Quote]:
        from app.domain.plans import LEGACY_PLAN_CODES, get_plan_name
        from app.services.checkout import resolve_purchase_amount

        amount = await resolve_purchase_amount(plan_code, months, int(telegram_id))
        if amount <= 0:
            return None
        code = (plan_code or "").lower().strip()
        return Quote(
            plan_code=code,
            months=int(months),
            amount_rub=int(amount),
            title=get_plan_name(code),
            is_legacy=code in LEGACY_PLAN_CODES,
        )

    async def start(self, telegram_id: int, quote: Quote, *, method: str = "yookassa", autorenew: bool = False) -> PaymentIntent:
        if method != "yookassa" or autorenew:
            raise _placeholder("CheckoutService.start(method/autorenew)", "A")
        intent = PaymentIntent(
            plan_code=quote.plan_code,
            months=quote.months,
            amount_rub=quote.amount_rub,
            telegram_id=int(telegram_id),
        )
        return await self._payments.create_payment(
            intent,
            description=f"CRS VPN {quote.title}",
            idempotence_key=f"checkout:{telegram_id}:{quote.plan_code}:{quote.months}",
        )

    async def check(self, telegram_id: int, payment_id: int) -> PaymentIntent:
        raise _placeholder("CheckoutService.check", "A")


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
