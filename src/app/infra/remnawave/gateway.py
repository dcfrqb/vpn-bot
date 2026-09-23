"""RemnaGateway over our own httpx client (stream B).

The only adapter new code uses to talk to the panel. It enforces the panel
invariants on every write (tests/invariants run against it):
  - manual squads (*-m, *-friend, arcadia) already on a user are kept and are
    never written by the bot (ValueError);
  - hwidDeviceLimit is never lowered; 0/None on the panel is left alone.

``update_user(squads=...)`` takes the FULL target list of non-manual squad
names (the port contract): callers that want "replace only the tariff squad"
compute that list themselves (see services/provisioning.target_squads).

Squad name -> uuid is cached per gateway for SQUAD_CACHE_TTL_S (06 L5): one
GET /api/internal-squads per 10 minutes instead of 2-3 per payment. A write
with an unknown squad name refreshes the cache once before failing.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Mapping, Optional, Sequence

from app.domain.models import DeviceInfo, PanelUser
from app.infra.remnawave.dto import (
    HwidDeviceDTO,
    UserDTO,
    devices_from_api,
    pick_primary,
    squads_from_api,
    unwrap,
    users_page_from_api,
)
from app.logger import logger

SQUAD_CACHE_TTL_S = 600
MAX_PAGE_SIZE = 1000


def _is_manual(name: Optional[str]) -> bool:
    from app.services.remna_tariff import is_manual_squad_name

    return is_manual_squad_name(name)


def _is_status(exc: BaseException, *codes: int) -> bool:
    import httpx

    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response is not None
        and exc.response.status_code in codes
    )


class HttpRemnaGateway:
    """RemnaGateway (app.services.ports) over app.infra.remnawave.client.RemnaClient.

    ``client_factory`` returns a RemnaClient-like object (tests pass the
    in-memory FakeRemna). ``clock`` is monotonic seconds for the squad cache.
    """

    def __init__(self, client_factory: Optional[Callable[[], Any]] = None, *,
                 clock: Callable[[], float] = time.monotonic):
        if client_factory is None:
            from app.infra.remnawave.client import RemnaClient

            client_factory = RemnaClient
        self._factory = client_factory
        self._clock = clock
        self._squads: Optional[dict[str, str]] = None
        self._squads_at: float = 0.0

    def _client(self):
        return self._factory()

    # ----------------------------------------------------------------- squads

    async def list_squads(self, *, refresh: bool = False) -> Mapping[str, str]:
        now = self._clock()
        if refresh or self._squads is None or now - self._squads_at > SQUAD_CACHE_TTL_S:
            raw = await self._client().list_internal_squads()
            self._squads = {s.name: s.uuid for s in squads_from_api({"response": {"internalSquads": raw}})}
            self._squads_at = now
        return dict(self._squads)

    def invalidate_squads(self) -> None:
        self._squads = None

    async def _names_to_uuids(self, names: Sequence[str]) -> list[str]:
        bad = [n for n in names if _is_manual(n)]
        if bad:
            raise ValueError(f"refusing to write manual squads {bad}")
        mapping = await self.list_squads()
        if any(n not in mapping for n in names):
            mapping = await self.list_squads(refresh=True)  # renamed/new squad
        missing = [n for n in names if n not in mapping]
        if missing:
            raise LookupError(f"squads not found in panel: {missing}")
        return [mapping[n] for n in names]

    async def _uuid_to_name(self) -> dict[str, str]:
        return {u: n for n, u in (await self.list_squads()).items()}

    # ------------------------------------------------------------------ users

    def _to_panel_user(self, dto: UserDTO, names: Optional[Mapping[str, str]] = None) -> PanelUser:
        return dto.to_panel_user(names)

    async def get_user(self, panel_id: int) -> Optional[PanelUser]:
        """None when the panel answers 404 (or 400 for a malformed id)."""
        try:
            data = await self._client().get_user_by_id(str(int(panel_id)))
        except ValueError:
            return None
        except Exception as e:
            if _is_status(e, 404, 400):
                return None
            raise
        dto = UserDTO.from_api(data)
        return self._to_panel_user(dto) if dto.id else None

    async def find_users_by_telegram_id(self, telegram_id: int) -> list[PanelUser]:
        """Every panel user bound to this Telegram id, primary first
        (ACTIVE, latest expireAt). Errors are raised: [] means "none"."""
        client = self._client()
        finder = getattr(client, "find_users_by_telegram_id", None)
        if finder is not None:
            raws = await finder(int(telegram_id))
        else:  # 2.x-shaped client (only the primary user)
            found = await client.get_user_by_telegram_id(int(telegram_id), strict=True)
            raws = [found.raw_data] if found and getattr(found, "raw_data", None) else []
        users = [UserDTO.from_api(r) for r in raws if isinstance(r, dict)]
        users = [u for u in users if u.id and u.telegram_id == int(telegram_id)]
        primary = pick_primary(users)
        ordered = ([primary] if primary else []) + [u for u in users if primary is None or u.id != primary.id]
        return [self._to_panel_user(u) for u in ordered]

    async def get_subscription_url(self, panel_id: int) -> Optional[str]:
        """Never log the result."""
        return await self._client().get_user_subscription_url(str(int(panel_id)))

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
        description: Optional[str] = None,
    ) -> PanelUser:
        uuids = await self._names_to_uuids(list(squads))
        data = await self._client().create_user(
            username,
            expire_at=expire_at,
            telegram_id=telegram_id,
            active_internal_squads=uuids or None,
            hwid_device_limit=device_limit,
            traffic_limit_bytes=traffic_limit_bytes,
            traffic_limit_strategy=traffic_limit_strategy,
            display_name=description,
        )
        return self._to_panel_user(UserDTO.from_api(data))

    async def create_user_unique(
        self,
        *,
        telegram_id: int,
        base_username: str,
        known_panel_id: Optional[int] = None,
        expire_at: datetime,
        squads: Sequence[str] = (),
        device_limit: Optional[int] = None,
        description: Optional[str] = None,
    ) -> tuple[PanelUser, bool]:
        """Create with the takeover-safe username policy of the hotfix (A-2):
        a taken name is reused only when it is our own user. (user, adopted)."""
        uuids = await self._names_to_uuids(list(squads))
        client = self._client()
        data, adopted = await client.create_user_unique(
            telegram_id=int(telegram_id),
            base_username=base_username,
            known_remna_id=str(known_panel_id) if known_panel_id else None,
            expire_at=expire_at,
            active_internal_squads=uuids or None,
            hwid_device_limit=device_limit,
            display_name=description,
        )
        return self._to_panel_user(UserDTO.from_api(data)), bool(adopted)

    async def update_user(
        self,
        panel_id: int,
        *,
        expire_at: Optional[datetime] = None,
        squads: Optional[Sequence[str]] = None,
        device_limit: Optional[int] = None,
        traffic_limit_bytes: Optional[int] = None,
        traffic_limit_strategy: Optional[str] = None,
        current: Optional[PanelUser] = None,
    ) -> PanelUser:
        """One PATCH with only the changed fields. ``current`` (already read)
        saves a GET. Manual squads on the user are kept; the limit never drops."""
        if current is None:
            current = await self.get_user(panel_id)
        if current is None:
            raise LookupError(f"panel user {panel_id} not found")
        kwargs: dict[str, Any] = {}
        if expire_at is not None:
            kwargs["expire_at"] = expire_at
        if squads is not None:
            target = await self._names_to_uuids(list(squads))
            by_uuid = await self._uuid_to_name()
            kept_manual = [u for u in current.squad_uuids if _is_manual(by_uuid.get(u))]
            merged = kept_manual + [u for u in target if u not in kept_manual]
            if set(merged) != set(current.squad_uuids):
                kwargs["activeInternalSquads"] = merged
        if device_limit is not None:
            cur = current.device_limit
            # Never lower; 0 = unlimited and set by hand, leave it.
            if cur is None or (int(cur) != 0 and int(cur) < int(device_limit)):
                kwargs["hwid_device_limit"] = int(device_limit)
        if traffic_limit_bytes is not None and traffic_limit_bytes != current.traffic_limit_bytes:
            kwargs["traffic_limit_bytes"] = int(traffic_limit_bytes)
        if traffic_limit_strategy is not None and traffic_limit_strategy != current.traffic_limit_strategy:
            kwargs["traffic_limit_strategy"] = traffic_limit_strategy
        if not kwargs:
            return current
        data = await self._client().update_user(str(int(panel_id)), **kwargs)
        dto = UserDTO.from_api(data)
        return self._to_panel_user(dto) if dto.id else current

    async def enable_user(self, panel_id: int) -> None:
        await self._client().enable_user(str(int(panel_id)))

    async def disable_user(self, panel_id: int) -> None:
        await self._client().disable_user(str(int(panel_id)))

    # ---------------------------------------------------------------- devices

    async def list_devices(self, panel_id: int) -> list[DeviceInfo]:
        data = await self._client().get_hwid_devices(int(panel_id))
        devices, _total = devices_from_api(data)
        return [d.to_device_info() for d in devices]

    async def delete_device(self, panel_id: int, hwid: str) -> bool:
        """True when the device was there and is gone now; False when unknown."""
        client = self._client()
        before, _ = devices_from_api(await client.get_hwid_devices(int(panel_id)))
        if all(d.hwid != hwid for d in before):
            return False
        try:
            data = await client.delete_hwid_device(int(panel_id), hwid)
        except Exception as e:
            if _is_status(e, 404):
                return False
            raise
        remaining, _ = devices_from_api(data)
        return all(d.hwid != hwid for d in remaining)

    async def iter_all_devices(self, page_size: int = 500) -> AsyncIterator[HwidDeviceDTO]:
        """Every HWID device of every user (GET /api/hwid/devices, offset paging)."""
        client = self._client()
        size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        start = 0
        while True:
            devices, total = devices_from_api(await client.list_all_hwid_devices(size=size, start=start))
            if not devices:
                return
            for d in devices:
                yield d
            start += len(devices)
            if (total is not None and start >= total) or len(devices) < size:
                return

    # ------------------------------------------------------------------- scan

    async def iter_users(self, page_size: int = 500) -> AsyncIterator[PanelUser]:
        """ALL panel users. ``start`` is an offset from 0 (06 L2)."""
        client = self._client()
        size = max(1, min(int(page_size), MAX_PAGE_SIZE))
        start = 0
        while True:
            users, total = users_page_from_api(await client.get_users(size=size, start=start))
            if not users:
                return
            for u in users:
                yield self._to_panel_user(u)
            start += len(users)
            if (total is not None and start >= total) or len(users) < size:
                return

    async def ping(self) -> bool:
        try:
            data = await self._client().health_check()
            return isinstance(unwrap(data), (dict, list))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"remna ping failed ({type(e).__name__})")
            return False
