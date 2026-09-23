"""Remnawave 3.4.3 DTOs (stream B).

Source: impl/remnawave_3.4.3_openapi.json (the contract of the panel the bot
talks to). Only the fields the bot reads are typed; the full JSON stays in
``raw`` (repr=False, never logged).

- ``UserDTO``          GET/PATCH/POST /api/users*, /api/users/stream items
- ``HwidDeviceDTO``    /api/hwid/devices*, items of ``devices``
- ``InternalSquadDTO`` GET /api/internal-squads items
- ``UPDATE_FIELDS`` / ``CREATE_FIELDS``: request bodies accepted by 3.4.3.
  ``name``, ``password`` and ``permissions`` do not exist in 3.x (zod strips
  them silently, review 06 L1); the human name goes to ``description``.

Parsing never raises on a missing optional field; a missing ``id`` gives 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from app.domain.models import DeviceInfo, PanelUser

# PATCH /api/users body (3.4.3). ``id`` is the numeric panel id.
UPDATE_FIELDS = frozenset({
    "id", "username", "status", "trafficLimitBytes", "trafficLimitStrategy", "expireAt",
    "description", "tag", "telegramId", "email", "hwidDeviceLimit", "activeInternalSquads",
    "externalSquadUuid",
})
# POST /api/users body (3.4.3). Required: username, expireAt.
CREATE_FIELDS = frozenset({
    "username", "status", "shortUuid", "trojanPassword", "vlessUuid", "ssPassword",
    "trafficLimitBytes", "trafficLimitStrategy", "expireAt", "createdAt", "lastTrafficResetAt",
    "description", "tag", "telegramId", "email", "hwidDeviceLimit", "activeInternalSquads",
    "externalSquadUuid",
})
USER_STATUSES = ("ACTIVE", "DISABLED", "LIMITED", "EXPIRED")
TRAFFIC_STRATEGIES = ("NO_RESET", "DAY", "WEEK", "MONTH", "MONTH_ROLLING")
# Statuses in which the nodes let the user in (LIMITED = cap reached, still valid).
LIVE_STATUSES = frozenset({"ACTIVE", "LIMITED"})
# expireAt at or after this year means "forever" (the bot writes 2099-12-31).
LIFETIME_YEAR = 2099
# Dates before this year are the "created without a subscription" sentinel (2000-01-01).
SENTINEL_YEAR_BEFORE = 2020


def parse_dt(value: Any) -> Optional[datetime]:
    """ISO string / datetime -> aware UTC datetime (None when empty or unparsable)."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def format_dt(value: datetime) -> str:
    """Aware/naive-UTC datetime -> the panel format ``YYYY-MM-DDTHH:MM:SSZ``."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    if value.year >= LIFETIME_YEAR:
        return "2099-12-31T23:59:59Z"
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def unwrap(data: Any) -> Any:
    """Every 3.4.3 response is ``{"response": ...}``; tolerate a bare body."""
    if isinstance(data, dict) and "response" in data:
        return data["response"]
    return data


def _int_or_none(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SquadRef:
    uuid: str
    name: Optional[str] = None


@dataclass(frozen=True)
class UserDTO:
    id: int
    username: str = ""
    short_uuid: str = ""
    status: Optional[str] = None
    telegram_id: Optional[int] = None
    expire_at: Optional[datetime] = None
    hwid_device_limit: Optional[int] = None
    traffic_limit_bytes: Optional[int] = None
    traffic_limit_strategy: Optional[str] = None
    used_traffic_bytes: Optional[int] = None
    description: Optional[str] = None
    tag: Optional[str] = None
    sub_revoked_at: Optional[datetime] = None
    online_at: Optional[datetime] = None
    first_connected_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    squads: tuple[SquadRef, ...] = ()
    subscription_url: Optional[str] = field(default=None, repr=False)
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_api(cls, data: Any) -> "UserDTO":
        raw = unwrap(data)
        if not isinstance(raw, dict):
            raw = {}
        squads: list[SquadRef] = []
        for item in raw.get("activeInternalSquads") or []:
            if isinstance(item, dict) and item.get("uuid"):
                squads.append(SquadRef(str(item["uuid"]), item.get("name")))
            elif isinstance(item, str):
                squads.append(SquadRef(item))
        traffic = raw.get("userTraffic") if isinstance(raw.get("userTraffic"), dict) else {}
        used = raw.get("usedTrafficBytes", traffic.get("usedTrafficBytes"))
        return cls(
            id=_int_or_none(raw.get("id")) or 0,
            username=str(raw.get("username") or ""),
            short_uuid=str(raw.get("shortUuid") or ""),
            status=raw.get("status"),
            telegram_id=_int_or_none(raw.get("telegramId")),
            expire_at=parse_dt(raw.get("expireAt")),
            hwid_device_limit=_int_or_none(raw.get("hwidDeviceLimit")),
            traffic_limit_bytes=_int_or_none(raw.get("trafficLimitBytes")),
            traffic_limit_strategy=raw.get("trafficLimitStrategy"),
            used_traffic_bytes=_int_or_none(used),
            description=raw.get("description"),
            tag=raw.get("tag"),
            sub_revoked_at=parse_dt(raw.get("subRevokedAt")),
            online_at=parse_dt(traffic.get("onlineAt")),
            first_connected_at=parse_dt(traffic.get("firstConnectedAt")),
            created_at=parse_dt(raw.get("createdAt")),
            updated_at=parse_dt(raw.get("updatedAt")),
            squads=tuple(squads),
            subscription_url=raw.get("subscriptionUrl") or None,
            raw=dict(raw),
        )

    # ------------------------------------------------------------ derived

    @property
    def is_lifetime(self) -> bool:
        return bool(self.expire_at and self.expire_at.year >= LIFETIME_YEAR)

    @property
    def has_subscription(self) -> bool:
        """False for the 2000-01-01 sentinel of users created at /start in 2.x."""
        return bool(self.expire_at and self.expire_at.year >= SENTINEL_YEAR_BEFORE)

    def is_live(self, now: Optional[datetime] = None) -> bool:
        """Nodes let this user in: status ACTIVE/LIMITED and expireAt in the future.

        ``subRevokedAt`` does not matter: in 3.x revoke only rotates the link (06 L6).
        """
        now = now or datetime.now(timezone.utc)
        return (self.status or "").upper() in LIVE_STATUSES and bool(self.expire_at and self.expire_at > now)

    def to_panel_user(self, uuid_to_name: Optional[Mapping[str, str]] = None) -> PanelUser:
        names = []
        for s in self.squads:
            n = s.name or (uuid_to_name or {}).get(s.uuid)
            if n:
                names.append(str(n))
        return PanelUser(
            id=self.id,
            uuid=str(self.raw.get("uuid") or ""),
            username=self.username,
            telegram_id=self.telegram_id,
            status=self.status,
            expire_at=self.expire_at,
            squads=tuple(names),
            squad_uuids=tuple(s.uuid for s in self.squads),
            device_limit=self.hwid_device_limit,
            traffic_limit_bytes=self.traffic_limit_bytes,
            traffic_limit_strategy=self.traffic_limit_strategy,
            used_traffic_bytes=self.used_traffic_bytes,
            subscription_url=self.subscription_url,
            raw=self.raw,
        )


@dataclass(frozen=True)
class HwidDeviceDTO:
    hwid: str = field(repr=False)
    user_id: Optional[int] = None
    platform: Optional[str] = None
    os_version: Optional[str] = None
    device_model: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> "HwidDeviceDTO":
        return cls(
            hwid=str(raw.get("hwid") or ""),
            user_id=_int_or_none(raw.get("userId")),
            platform=raw.get("platform"),
            os_version=raw.get("osVersion"),
            device_model=raw.get("deviceModel"),
            user_agent=raw.get("userAgent"),
            created_at=parse_dt(raw.get("createdAt")),
            updated_at=parse_dt(raw.get("updatedAt")),
        )

    def to_device_info(self) -> DeviceInfo:
        # requestIp is deliberately not carried over (personal data, not needed).
        return DeviceInfo(
            hwid=self.hwid,
            platform=self.platform,
            os_version=self.os_version,
            device_model=self.device_model,
            user_agent=self.user_agent,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


def devices_from_api(data: Any) -> tuple[list[HwidDeviceDTO], Optional[int]]:
    """``{"response": {"total", "devices": [...]}}`` -> (devices, total)."""
    body = unwrap(data)
    if not isinstance(body, dict):
        return [], None
    items = body.get("devices") or []
    return [HwidDeviceDTO.from_api(d) for d in items if isinstance(d, dict) and d.get("hwid")], _int_or_none(
        body.get("total")
    )


@dataclass(frozen=True)
class InternalSquadDTO:
    uuid: str
    name: str
    members: Optional[int] = None

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> "InternalSquadDTO":
        info = raw.get("info") if isinstance(raw.get("info"), dict) else {}
        return cls(uuid=str(raw.get("uuid") or ""), name=str(raw.get("name") or ""),
                   members=_int_or_none(info.get("membersCount")))


def squads_from_api(data: Any) -> list[InternalSquadDTO]:
    body = unwrap(data)
    if isinstance(body, dict):
        items = body.get("internalSquads", body.get("items")) or []
    elif isinstance(body, list):
        items = body
    else:
        items = []
    return [InternalSquadDTO.from_api(s) for s in items if isinstance(s, dict) and s.get("uuid") and s.get("name")]


def users_page_from_api(data: Any) -> tuple[list[UserDTO], Optional[int]]:
    """GET /api/users -> (users, total)."""
    body = unwrap(data)
    if not isinstance(body, dict):
        return [], None
    return [UserDTO.from_api(u) for u in body.get("users") or [] if isinstance(u, dict)], _int_or_none(
        body.get("total")
    )


def pick_primary(users: list[UserDTO]) -> Optional[UserDTO]:
    """Several panel users with one telegramId: ACTIVE first, then the latest
    expireAt, then the smaller id (06 M3)."""
    if not users:
        return None

    def key(u: UserDTO):
        return (
            1 if (u.status or "").upper() == "ACTIVE" else 0,
            u.expire_at or datetime.min.replace(tzinfo=timezone.utc),
            -u.id,
        )

    return max(users, key=key)


# --- raw panel JSON -> domain PanelUser (moved from services/shims.py at cutover) ---


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
