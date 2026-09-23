"""Ports implemented over 2.1.1 code (release 3.0 Foundation).

These adapters let new routers/jobs code against app.services.ports today.
Each owning stream replaces its shim with a real implementation and rewires
app.container; the shim is deleted at cutover.

What is real here and what is a placeholder:

| Port                | Shim                       | State                                   |
|---------------------|----------------------------|-----------------------------------------|
| RemnaGateway        | LegacyRemnaGateway         | real over RemnaClient, devices -> B     |
| PaymentGateway      | LegacyPaymentGateway       | = infra.yookassa.YooKassaGateway (A)    |
| StarsGateway        | DisabledStarsGateway       | placeholder; money swaps in the real one|
| ProvisioningService | LegacyProvisioningService  | paid periods + revoke over 2.x (A) -> B |
| StatusService       | LegacyStatusService        | real over services.users (+cache)       |
| DevicesService      | UnavailableDevicesService  | empty list / False -> B                 |
| CheckoutService     | LegacyCheckoutService      | -> services.checkout (A)                |
| PromoService        | LegacyPromoService         | placeholder -> E                        |
| MaintenanceGuard    | RedisMaintenanceGuard      | real (manual Redis flag)                |
| Notifier            | notifications.TelegramNotifier (not a shim)                          |

Placeholders raise NotImplementedError("<port>: owned by stream X"), so a
premature call fails loudly in tests instead of silently doing nothing.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Mapping, Optional, Sequence

from app.domain.models import (
    DeviceInfo,
    Entitlement,
    PanelUser,
    PaymentIntent,
    PromoReward,
    Quote,
    SubKind,
    SubscriptionState,
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


class LegacyRemnaGateway:
    """RemnaGateway over app.remnawave.client.RemnaClient.

    Enforces the two panel invariants on writes:
      - manual squads (*-m, *-friend, arcadia) already on the user are kept,
        and are never added by the bot;
      - hwidDeviceLimit is never lowered.
    """

    def __init__(self, client_factory: Optional[Callable[[], Any]] = None):
        if client_factory is None:
            from app.remnawave.client import RemnaClient

            client_factory = RemnaClient
        self._factory = client_factory

    def _client(self):
        return self._factory()

    async def list_squads(self) -> Mapping[str, str]:
        squads = await self._client().list_internal_squads()
        return {s.get("name"): s.get("uuid") for s in squads if isinstance(s, dict) and s.get("name")}

    async def get_user(self, panel_id: int) -> Optional[PanelUser]:
        import httpx

        try:
            data = await self._client().get_user_by_id(str(int(panel_id)))
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise
        raw = _unwrap(data)
        return panel_user_from_raw(raw) if raw else None

    async def find_users_by_telegram_id(self, telegram_id: int) -> list[PanelUser]:
        client = self._client()
        found = await client.get_user_by_telegram_id(int(telegram_id), strict=True)
        if not found:
            return []
        raw = getattr(found, "raw_data", None) or {}
        return [panel_user_from_raw(raw)] if raw else []

    async def get_subscription_url(self, panel_id: int) -> Optional[str]:
        return await self._client().get_user_subscription_url(str(int(panel_id)))

    async def _names_to_uuids(self, names: Sequence[str]) -> list[str]:
        from app.services.remna_tariff import is_manual_squad_name

        bad = [n for n in names if is_manual_squad_name(n)]
        if bad:
            raise ValueError(f"refusing to write manual squads {bad}")
        mapping = await self.list_squads()
        missing = [n for n in names if n not in mapping]
        if missing:
            raise LookupError(f"squads not found in panel: {missing}")
        return [mapping[n] for n in names]

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
        from app.services.payments.yookassa import generate_remna_password

        uuids = await self._names_to_uuids(list(squads))
        data = await self._client().create_user(
            username,
            generate_remna_password(),
            expire_at=expire_at,
            telegram_id=telegram_id,
            active_internal_squads=uuids or None,
            hwid_device_limit=device_limit,
            traffic_limit_bytes=traffic_limit_bytes,
            traffic_limit_strategy=traffic_limit_strategy,
        )
        return panel_user_from_raw(_unwrap(data))

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
        from app.services.remna_tariff import is_manual_squad_name

        current = await self.get_user(panel_id)
        if current is None:
            raise LookupError(f"panel user {panel_id} not found")
        kwargs: dict[str, Any] = {}
        if expire_at is not None:
            kwargs["expire_at"] = expire_at
        if squads is not None:
            target = await self._names_to_uuids(list(squads))
            mapping = await self.list_squads()
            by_uuid = {u: n for n, u in mapping.items()}
            kept_manual = [u for u in current.squad_uuids if is_manual_squad_name(by_uuid.get(u))]
            merged = kept_manual + [u for u in target if u not in kept_manual]
            if set(merged) != set(current.squad_uuids):
                kwargs["activeInternalSquads"] = merged
        if device_limit is not None:
            cur = current.device_limit
            # Never lower: 0/None on the panel = unlimited / manual, leave it.
            if cur is not None and (int(cur) == 0 or int(cur) >= int(device_limit)):
                pass
            else:
                kwargs["hwid_device_limit"] = int(device_limit)
        if traffic_limit_bytes is not None:
            kwargs["traffic_limit_bytes"] = int(traffic_limit_bytes)
        if traffic_limit_strategy is not None:
            kwargs["traffic_limit_strategy"] = traffic_limit_strategy
        if not kwargs:
            return current
        data = await self._client().update_user(str(int(panel_id)), **kwargs)
        raw = _unwrap(data)
        return panel_user_from_raw(raw) if raw else current

    async def enable_user(self, panel_id: int) -> None:
        await self._client().enable_user(str(int(panel_id)))

    async def disable_user(self, panel_id: int) -> None:
        await self._client().disable_user(str(int(panel_id)))

    async def list_devices(self, panel_id: int) -> list[DeviceInfo]:
        raise _placeholder("RemnaGateway.list_devices", "B")

    async def delete_device(self, panel_id: int, hwid: str) -> bool:
        raise _placeholder("RemnaGateway.delete_device", "B")

    async def iter_users(self, page_size: int = 500) -> AsyncIterator[PanelUser]:
        client = self._client()
        start = 1
        seen = 0
        while True:
            data = await client.get_users(size=page_size, start=start)
            response = data.get("response", {}) if isinstance(data, dict) else {}
            users = response.get("users", []) if isinstance(response, dict) else []
            total = response.get("total") if isinstance(response, dict) else None
            if not users:
                return
            for raw in users:
                yield panel_user_from_raw(raw)
            seen += len(users)
            if (total is not None and seen >= int(total)) or len(users) < page_size:
                return
            start += page_size

    async def ping(self) -> bool:
        try:
            await self._client().health_check()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"remna ping failed ({type(e).__name__})")
            return False


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


class LegacyProvisioningService:
    """ProvisioningService over the 2.x provisioning core (stream A adapter in
    app.services.payments.legacy_provisioning): paid main periods and revoke.
    Stream B replaces it with its own service in the container."""

    async def grant(self, telegram_id: int, entitlement: Entitlement, *, trace_id: str) -> SubscriptionState:
        if entitlement is None or not getattr(entitlement, "payment_id", None):
            raise _placeholder("ProvisioningService.grant (non-payment entitlements)", "B")
        from app.services.payments.legacy_provisioning import grant

        return await grant(telegram_id, entitlement, trace_id=trace_id)

    async def revoke(self, telegram_id: int, *, sub_kind: SubKind = SubKind.MAIN, reason: str, trace_id: str) -> bool:
        from app.services.payments.legacy_provisioning import revoke

        return await revoke(telegram_id, sub_kind=sub_kind, reason=reason, trace_id=trace_id)


class LegacyStatusService:
    """StatusService over services.users.get_user_active_subscription."""

    async def get_state(self, telegram_id: int, *, force: bool = False) -> SubscriptionState:
        from app.services.users import get_user_active_subscription

        info = await get_user_active_subscription(int(telegram_id), use_cache=not force)
        now = datetime.now(timezone.utc)
        if info is None:
            return SubscriptionState(telegram_id=int(telegram_id), fetched_at=now)
        return SubscriptionState(
            telegram_id=int(telegram_id),
            has_panel_user=bool(info.remna_user_id),
            active=bool(info.active),
            plan_code=info.plan_code or None,
            expires_at=_parse_dt(info.valid_until),
            fetched_at=now,
        )

    async def invalidate(self, telegram_id: int) -> None:
        from app.services.cache import invalidate_sync_cache, invalidate_user_cache

        await invalidate_sync_cache(int(telegram_id))
        await invalidate_user_cache(int(telegram_id))


class UnavailableDevicesService:
    async def list_devices(self, telegram_id: int) -> list[DeviceInfo]:
        return []

    async def unlink(self, telegram_id: int, device_short_id: str) -> bool:
        return False

    async def unlinks_left(self, telegram_id: int) -> int:
        return 0


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
