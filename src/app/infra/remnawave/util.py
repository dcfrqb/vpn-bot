"""Pure helpers of the Remnawave client (stream B): ids, dates, PATCH
payloads, error classification, plan from squads. Re-exported from
app.infra.remnawave.client (and so from the old app.remnawave.client path).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Union

import httpx

from app.infra.remnawave.dto import UPDATE_FIELDS, parse_dt
from app.logger import logger

# "Forever": Remnawave has no null expiry, the bot writes this date.
LIFETIME_EXPIRE_AT = "2099-12-31T23:59:59Z"
# Created without a subscription (2.x /start users); < 2020 means "none".
NO_SUBSCRIPTION_SENTINEL = "2000-01-01T00:00:00Z"


# snake_case / camelCase kwargs -> PATCH /api/users fields (3.4.3, see dto.UPDATE_FIELDS).
_USER_UPDATE_WHITELIST = {
    "username": "username",
    "status": "status",
    "description": "description",
    "tag": "tag",
    "email": "email",
    "expire_at": "expireAt",
    "expireAt": "expireAt",
    "telegram_id": "telegramId",
    "telegramId": "telegramId",
    "active_internal_squads": "activeInternalSquads",
    "activeInternalSquads": "activeInternalSquads",
    "hwid_device_limit": "hwidDeviceLimit",
    "hwidDeviceLimit": "hwidDeviceLimit",
    "traffic_limit_bytes": "trafficLimitBytes",
    "trafficLimitBytes": "trafficLimitBytes",
    "traffic_limit_strategy": "trafficLimitStrategy",
    "trafficLimitStrategy": "trafficLimitStrategy",
    "external_squad_uuid": "externalSquadUuid",
    "externalSquadUuid": "externalSquadUuid",
}
assert set(_USER_UPDATE_WHITELIST.values()) <= UPDATE_FIELDS


def panel_id(user_id: Any) -> int:
    """Numeric Remnawave 3.x user id or ValueError (legacy UUIDs are rejected
    before a request is made, 06 L3)."""
    if isinstance(user_id, bool):
        raise ValueError(f"not a panel id: {user_id!r}")
    try:
        value = int(str(user_id).strip())
    except (TypeError, ValueError):
        raise ValueError(f"not a numeric panel id: {user_id!r}") from None
    if value <= 0:
        raise ValueError(f"not a positive panel id: {user_id!r}")
    return value


def normalize_expire_at(value: Optional[Union[str, datetime, date]]) -> Optional[str]:
    """expireAt for the API: None -> None, year >= 2099 -> LIFETIME_EXPIRE_AT,
    otherwise ISO8601 UTC with ``Z`` (naive datetimes are UTC)."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        if getattr(value, "year", 0) >= 2099:
            return LIFETIME_EXPIRE_AT
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return datetime(value.year, value.month, value.day, 23, 59, 59, tzinfo=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    if isinstance(value, str):
        if "2099" in value:
            return LIFETIME_EXPIRE_AT
        dt = parse_dt(value)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else value
    return None


def build_user_payload_from_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """PATCH payload from kwargs: whitelist + snake_case -> camelCase, None skipped.
    ``expireAt`` wins over ``expire_at``. Unknown fields (``name``, ``password``,
    ``permissions``: absent in 3.x) are dropped with a debug line."""
    result: Dict[str, Any] = {}
    expire_val = kwargs.get("expireAt", kwargs.get("expire_at"))
    if expire_val is not None:
        normalized = normalize_expire_at(expire_val)
        if normalized is not None:
            result["expireAt"] = normalized
    for key, val in kwargs.items():
        if val is None or key in ("expire_at", "expireAt"):
            continue
        api_key = _USER_UPDATE_WHITELIST.get(key)
        if api_key is None:
            logger.debug(f"Remna update_user: field {key!r} is not in the 3.4.3 contract, dropped")
            continue
        if api_key in ("telegramId", "trafficLimitBytes", "hwidDeviceLimit"):
            result[api_key] = int(val)
        elif api_key == "activeInternalSquads":
            result[api_key] = list(val) if isinstance(val, (list, tuple)) else [val]
        else:
            result[api_key] = val
    return result


def is_username_taken_error(exc: Exception) -> bool:
    """True when Remnawave answered "username already exists" (A019)."""
    if not isinstance(exc, httpx.HTTPStatusError):
        text = str(exc).lower()
        return "already exists" in text or "a019" in text
    try:
        body = exc.response.text or ""
    except Exception:
        body = ""
    status = exc.response.status_code if exc.response is not None else 0
    low = body.lower()
    return status in (400, 409) and ("exists" in low or "a019" in low)


def is_not_found_error(exc: BaseException) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response is not None
        and exc.response.status_code == 404
    )


def is_own_remna_user(
    user_data: Dict[str, Any], telegram_id: int, known_remna_id: Optional[str] = None
) -> bool:
    """A panel user found by username is "ours" only if its telegramId equals
    ours, or telegramId is empty and its id is the one stored in our DB.
    Anything else is somebody else's account (reused @nick, namesake)."""
    if not isinstance(user_data, dict):
        return False
    tg = user_data.get("telegramId")
    if tg not in (None, "", 0):
        try:
            return int(tg) == int(telegram_id)
        except (TypeError, ValueError):
            return False
    if known_remna_id is None:
        return False
    uid = user_data.get("id") or user_data.get("uuid")
    return uid is not None and str(uid) == str(known_remna_id)


def plan_from_squad_names(names) -> Optional[str]:
    """Tariff plan code from squad names (06 L6: ``raw['plan']`` does not exist).
    Manual variants count as their base plan: ``pro-m``/``pro-friend`` -> pro."""
    from app.domain.plans import PLAN_CATALOG

    catalog = {c for c, m in PLAN_CATALOG.items() if m.get("squad") and c != "trial"}
    for raw in names or ():
        n = str(raw or "").lower()
        for suffix in ("-friend", "-m"):
            if n.endswith(suffix):
                n = n[: -len(suffix)]
                break
        if n in catalog:
            return n
    return None
