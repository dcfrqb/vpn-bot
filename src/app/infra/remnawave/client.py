"""Remnawave 3.4.3 HTTP client (own httpx, no SDK). Owner: stream B.

Moved from app.remnawave.client in 3.0 (the old path is an alias of this
module). New code talks to the panel through app.infra.remnawave.gateway
(RemnaGateway port); this class stays the transport underneath and keeps the
2.x surface the old code still calls until the cutover.

Fixes from review 06 applied here:
- M3: a panel error is not "user not found". ``_find_user_by_username``
  re-raises everything except 404, so ``create_user_unique`` never treats an
  outage as "the name belongs to someone else" and never creates a duplicate.
  ``get_user_by_telegram_id(strict=True)`` and ``find_users_by_telegram_id``
  return None/[] only for 404 or an empty list.
- L1: 3.x has no ``name``/``password``/``permissions``; the display name goes
  to ``description``. The no-op "update name" PATCH on every lookup is gone.
- L2: ``/api/users`` ``start`` is an OFFSET starting at 0.
- L3: panel ids are validated as positive integers before any request.
- L4: 404 is logged at DEBUG (it is an expected answer); UI reads use
  ``RemnaClient.for_ui()`` (8 s, 1 retry); default timeout 15 s / connect 5 s.
- L6: "active" = status ACTIVE/LIMITED and expireAt in the future;
  ``subRevokedAt`` only rotates the link in 3.x. The plan comes from squads.
- L7: dead methods removed (api-token CRUD, ``create_user_with_name``,
  multi-shape subscription URL probing).
Subscription URLs are never logged (invariant 6).
"""
import asyncio
import re
import secrets as _secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Union

import httpx

from app.config import settings
from app.core.errors import InfraError
from app.infra.remnawave.dto import UPDATE_FIELDS, LIVE_STATUSES, SENTINEL_YEAR_BEFORE, parse_dt, unwrap
from app.logger import logger

# "Forever": Remnawave has no null expiry, the bot writes this date.
LIFETIME_EXPIRE_AT = "2099-12-31T23:59:59Z"
# Created without a subscription (2.x /start users); < 2020 means "none".
NO_SUBSCRIPTION_SENTINEL = "2000-01-01T00:00:00Z"

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
UI_TIMEOUT = httpx.Timeout(8.0, connect=4.0)

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


def pick_primary_remna_user(users: list) -> Optional[Dict[str, Any]]:
    """Several users with one telegramId: ACTIVE first, then latest expireAt,
    then the smaller id."""
    candidates = [u for u in users if isinstance(u, dict)]
    if not candidates:
        return None

    def _key(u: Dict[str, Any]):
        active = 1 if u.get("status") == "ACTIVE" else 0
        expire = str(u.get("expireAt") or "")
        try:
            uid = -int(u.get("id") or 0)
        except (TypeError, ValueError):
            uid = 0
        return (active, expire, uid)

    return max(candidates, key=_key)


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


@dataclass
class RemnaUser:
    """2.x DTO. ``uuid`` holds the numeric 3.x id as a string (kept for callers)."""

    uuid: str
    telegram_id: Optional[int]
    username: Optional[str]
    name: Optional[str]
    raw_data: Dict[str, Any]


@dataclass
class RemnaSubscription:
    active: bool
    expires_at: Optional[datetime]  # naive UTC, as 2.x callers expect
    plan: Optional[str]
    raw_data: Dict[str, Any]


# One pooled httpx client per process.
_shared_http_client: Optional[httpx.AsyncClient] = None


def get_shared_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None:
        _shared_http_client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )
        logger.info("Remna API: shared HTTP client created")
    return _shared_http_client


async def close_shared_http_client():
    global _shared_http_client
    if _shared_http_client:
        await _shared_http_client.aclose()
        _shared_http_client = None
        logger.info("Remna API: shared HTTP client closed")


def _subscription_base() -> Optional[str]:
    return str(settings.SUBSCRIPTION_BASE_URL).rstrip("/") if settings.SUBSCRIPTION_BASE_URL else None


def apply_subscription_domain(url: Optional[str]) -> Optional[str]:
    """SUBSCRIPTION_BASE_URL overrides the host of the panel's subscriptionUrl."""
    if not url:
        return url
    base = _subscription_base()
    if not base:
        return url
    from urllib.parse import urlparse

    try:
        parsed, sub = urlparse(url), urlparse(base)
        if parsed.netloc and parsed.netloc != sub.netloc:
            return url.replace(f"{parsed.scheme}://{parsed.netloc}", base, 1)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"SUBSCRIPTION_BASE_URL override failed ({type(e).__name__})")
    return url


class RemnaClient:
    def __init__(self, max_retries: int = 3, initial_delay: float = 1.0, max_delay: float = 60.0,
                 use_shared_client: bool = True, timeout: Optional[httpx.Timeout] = None):
        base_url = settings.remna_base_url or settings.REMNA_API_BASE
        self.base_url = str(base_url).rstrip("/") if base_url else None
        self.api_key = settings.remna_api_token or settings.REMNA_API_KEY
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.use_shared_client = use_shared_client
        self.timeout = timeout
        self._own_client: Optional[httpx.AsyncClient] = None

    @classmethod
    def for_ui(cls) -> "RemnaClient":
        """Short timeout and one retry: a button press must not hang for minutes (06 L4)."""
        return cls(max_retries=1, initial_delay=0.5, timeout=UI_TIMEOUT)

    @property
    def client(self) -> httpx.AsyncClient:
        if self.use_shared_client:
            return get_shared_http_client()
        if self._own_client is None:
            self._own_client = httpx.AsyncClient(
                timeout=self.timeout or DEFAULT_TIMEOUT,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._own_client

    async def request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """HTTP call with bearer token and retry (5xx, 429, network errors)."""
        if not self.api_key:
            raise ValueError("REMNA_API_KEY is not configured")

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_key}"
        headers["Content-Type"] = "application/json"
        if self.timeout is not None and "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout

        if endpoint.startswith("/api") and self.base_url and self.base_url.rstrip("/").endswith("/api"):
            endpoint = endpoint[4:]
        url = f"{self.base_url}{endpoint}"

        last_exception = None
        delay = self.initial_delay
        for attempt in range(self.max_retries + 1):
            try:
                if attempt > 0:
                    logger.warning(f"Remna retry {attempt}/{self.max_retries} {method} {endpoint} (delay {delay:.2f}s)")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self.max_delay)
                resp = await self.client.request(method, url, headers=headers, **kwargs)
                resp.raise_for_status()
                if attempt > 0:
                    logger.info(f"Remna {method} {endpoint} succeeded after {attempt} retries")
                # 3.x: DELETE answers 204, background/bulk actions 202, both without a body.
                if resp.status_code in (202, 204) or not resp.content:
                    return {}
                return resp.json()
            except httpx.HTTPStatusError as e:
                last_exception = e
                status_code = e.response.status_code
                if 400 <= status_code < 500:
                    if status_code == 429 and attempt < self.max_retries:
                        logger.warning(f"Remna rate limit (429), retry in {delay:.2f}s")
                        continue
                    if status_code == 404:
                        logger.debug(f"Remna 404 {method} {endpoint}")
                    else:
                        logger.error(f"Remna HTTP {status_code} {method} {endpoint}: {e.response.text[:300]}")
                    raise
                if attempt < self.max_retries:
                    logger.warning(f"Remna HTTP {status_code} {method} {endpoint}, retry in {delay:.2f}s")
                    continue
                logger.error(f"Remna HTTP {status_code} {method} {endpoint} after {self.max_retries} retries")
                raise
            except httpx.RequestError as e:
                last_exception = e
                if attempt < self.max_retries:
                    logger.warning(f"Remna network error ({type(e).__name__}) {method} {endpoint}, retry in {delay:.2f}s")
                    continue
                logger.error(f"Remna network error ({type(e).__name__}) {method} {endpoint} after {self.max_retries} retries")
                raise InfraError(
                    message=f"Remna API connection error: {type(e).__name__}",
                    service="remna",
                    details=f"{method} {endpoint}, attempts: {self.max_retries + 1}",
                ) from e
        if last_exception:
            raise InfraError(
                message=f"Remna API unreachable after {self.max_retries + 1} attempts",
                service="remna",
                details=type(last_exception).__name__,
            ) from last_exception
        raise RuntimeError("unexpected state in the retry loop")

    async def close(self):
        if not self.use_shared_client and self._own_client:
            await self._own_client.aclose()
            self._own_client = None

    def _sanitize_display_name(self, name: Optional[str], telegram_id: int) -> str:
        """Human name for the panel ``description`` (max 64 chars)."""
        if not name or not name.strip():
            return f"User {telegram_id}"
        clean_name = " ".join(name.split())
        if len(clean_name) > 64:
            clean_name = clean_name[:61] + "..."
        if not re.sub(r"[^\w\s\-\.]", "", clean_name, flags=re.UNICODE).strip():
            return f"User {telegram_id}"
        return clean_name

    # ------------------------------------------------------------------ users

    async def get_users(self, size: int = 50, start: int = 0) -> Dict[str, Any]:
        """GET /api/users page. ``start`` is an offset from 0 (06 L2); size <= 1000."""
        size = max(1, min(int(size), 1000))
        return await self.request("GET", f"/api/users?size={size}&start={max(0, int(start))}")

    async def create_user(
        self,
        username: str,
        password: Optional[str] = None,
        expire_at: Optional[Union[str, datetime, date]] = None,
        telegram_id: Optional[int] = None,
        active_internal_squads: Optional[list] = None,
        display_name: Optional[str] = None,
        hwid_device_limit: Optional[int] = None,
        traffic_limit_bytes: Optional[int] = None,
        traffic_limit_strategy: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /api/users. ``password`` is accepted for 2.x callers and ignored
        (3.x has no such field). ``display_name`` goes to ``description``.
        Without ``expire_at`` the 2000-01-01 sentinel ("no subscription") is used."""
        payload: Dict[str, Any] = {
            "username": username,
            "expireAt": normalize_expire_at(expire_at) or NO_SUBSCRIPTION_SENTINEL,
        }
        if telegram_id:
            payload["telegramId"] = int(telegram_id)
        if active_internal_squads:
            payload["activeInternalSquads"] = list(active_internal_squads)
        if description or display_name:
            payload["description"] = description or display_name
        if hwid_device_limit is not None:
            payload["hwidDeviceLimit"] = int(hwid_device_limit)
        if traffic_limit_bytes is not None:
            payload["trafficLimitBytes"] = int(traffic_limit_bytes)
        if traffic_limit_strategy is not None:
            payload["trafficLimitStrategy"] = traffic_limit_strategy
        return await self.request("POST", "/api/users", json=payload)

    async def create_user_unique(
        self,
        *,
        telegram_id: int,
        base_username: str,
        known_remna_id: Optional[str] = None,
        **create_kwargs,
    ) -> tuple:
        """Create a panel user; a taken username NEVER hands over someone else's account.

        Candidates: base_username -> tg_<id> -> tg_<id>_<rand>. On "username
        taken" the owner is checked: our own user (same telegramId, or the id
        stored in our DB) is returned with adopted=True; otherwise the next
        candidate is tried. A panel error while checking the owner is raised
        (06 M3): "cannot tell" is not "someone else's".
        Returns (user_data: dict, adopted: bool).
        """
        candidates = [base_username]
        fallback = f"tg_{int(telegram_id)}"
        if fallback not in candidates:
            candidates.append(fallback)
        candidates.append(f"tg_{int(telegram_id)}_{_secrets.token_hex(3)}")

        last_exc: Optional[Exception] = None
        for name in candidates:
            try:
                response = await self.create_user(username=name, telegram_id=telegram_id, **create_kwargs)
                user_data = response.get("response", response) if isinstance(response, dict) else response
                return user_data, False
            except Exception as e:
                if not is_username_taken_error(e):
                    raise
                last_exc = e
                existing = await self._find_user_by_username(name)
                if existing and is_own_remna_user(existing, telegram_id, known_remna_id):
                    logger.info(
                        f"Remna username {name} already belongs to tg={telegram_id} "
                        f"(id={existing.get('id')}), reusing it"
                    )
                    return existing, True
                logger.warning(
                    f"Remna username {name} is taken by another user (id={(existing or {}).get('id')}), "
                    f"trying another username for tg={telegram_id}"
                )
        assert last_exc is not None
        raise last_exc

    async def create_obhod_user(
        self,
        username: str,
        password: Optional[str],
        expire_at: Optional[Union[str, datetime, date]],
        active_internal_squads: list,
        traffic_limit_bytes: int,
        traffic_limit_strategy: str,
        display_name: Optional[str] = None,
        hwid_device_limit: Optional[int] = None,
    ) -> str:
        """Create the obhod user WITHOUT telegramId and return its id.

        No telegramId on purpose: otherwise the telegramId lookup of the main
        user becomes ambiguous. The obhod user is addressed only by the stored id.
        """
        response = await self.create_user(
            username=username,
            expire_at=expire_at,
            telegram_id=None,
            active_internal_squads=active_internal_squads,
            display_name=display_name,
            hwid_device_limit=hwid_device_limit,
            traffic_limit_bytes=traffic_limit_bytes,
            traffic_limit_strategy=traffic_limit_strategy,
        )
        user_data = response.get("response", response) if isinstance(response, dict) else response
        uid = (user_data.get("id") or user_data.get("uuid")) if isinstance(user_data, dict) else None
        if not uid:
            raise ValueError("create_obhod_user: no id in the panel response")
        logger.info(f"Remna obhod user created: id={uid} username={username}")
        return str(uid)

    async def get_user_traffic_info(self, user_id: str) -> Dict[str, Any]:
        """Traffic/limit of a user: used_bytes, limit_bytes (0 = unlimited),
        strategy, expire_at (raw string), status."""
        data = await self.get_user_by_id(user_id)
        raw = unwrap(data)
        if not isinstance(raw, dict):
            raw = {}
        return {
            "used_bytes": (raw.get("userTraffic") or {}).get("usedTrafficBytes"),
            "limit_bytes": raw.get("trafficLimitBytes"),
            "strategy": raw.get("trafficLimitStrategy"),
            "expire_at": raw.get("expireAt"),
            "status": raw.get("status"),
        }

    async def get_or_create_user(
        self,
        telegram_id: int,
        name: str = "",
        expire_at: Optional[str] = None,
        tg_username: Optional[str] = None,
        tg_first_name: Optional[str] = None,
        tg_last_name: Optional[str] = None,
    ) -> RemnaUser:
        """Find by telegramId, create when absent (2.x grant paths only; in 3.0
        accounts are created only by ProvisioningService).

        The lookup is strict: a panel outage raises instead of creating a duplicate.
        """
        from app.utils.remna_username import build_remna_display_name, build_remna_username

        username = build_remna_username(
            telegram_id=telegram_id, username=tg_username, first_name=tg_first_name, last_name=tg_last_name,
        )
        display_name = build_remna_display_name(
            telegram_id=telegram_id, username=tg_username, first_name=tg_first_name, last_name=tg_last_name,
        ) if (tg_username or tg_first_name or tg_last_name) else self._sanitize_display_name(
            name or f"User {telegram_id}", telegram_id
        )

        existing = await self.get_user_by_telegram_id(telegram_id, strict=True)
        if existing:
            return existing

        user_data, adopted = await self.create_user_unique(
            telegram_id=telegram_id,
            base_username=username,
            expire_at=expire_at,
            display_name=display_name,
        )
        uid = (user_data.get("id") or user_data.get("uuid")) if isinstance(user_data, dict) else None
        if not uid:
            raise ValueError("no id in the panel response")
        if adopted and not user_data.get("telegramId"):
            # Our own user (matched by the stored id) without telegramId: bind it.
            await self.update_user(str(uid), telegramId=telegram_id)
        logger.info(
            f"Remna user {'reused' if adopted else 'created'}: id={uid} tg={telegram_id} "
            f"username={user_data.get('username')}"
        )
        return RemnaUser(
            uuid=str(uid),
            telegram_id=telegram_id,
            username=user_data.get("username") or username,
            name=display_name,
            raw_data=user_data,
        )

    async def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """User by username, None on 404. Other panel errors are raised (06 M3)."""
        return await self._find_user_by_username(username)

    async def _find_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        try:
            response = await self.request("GET", f"/api/users/by-username/{username}")
        except httpx.HTTPStatusError as e:
            if is_not_found_error(e):
                return None
            raise
        user_data = unwrap(response)
        return user_data if isinstance(user_data, dict) and user_data else None

    async def delete_user(self, user_id: str) -> Dict[str, Any]:
        return await self.request("DELETE", f"/api/users/{panel_id(user_id)}")

    async def disable_user(self, user_id: str) -> Dict[str, Any]:
        """status=DISABLED (reversible with enable_user)."""
        return await self.request("POST", f"/api/users/{panel_id(user_id)}/actions/disable")

    async def enable_user(self, user_id: str) -> Dict[str, Any]:
        return await self.request("POST", f"/api/users/{panel_id(user_id)}/actions/enable")

    async def get_user_by_id(self, user_id: str) -> Dict[str, Any]:
        return await self.request("GET", f"/api/users/{panel_id(user_id)}")

    async def find_users_by_telegram_id(self, telegram_id: int) -> list:
        """ALL panel users with this telegramId (strict: errors are raised).

        3.x removed /users/by-telegram-id; /users/stream filters by telegramId.
        The filter is re-checked here (review m-2): a panel upgrade that ignores
        the filter must not hand us other people's accounts.
        """
        response = await self.request("GET", f"/api/users/stream?telegramId={int(telegram_id)}&size=25")
        body = unwrap(response)
        if isinstance(body, dict) and "users" in body:
            body = body["users"]
        if isinstance(body, dict):
            body = [body] if body else []
        if not isinstance(body, list):
            return []
        matched = [u for u in body if isinstance(u, dict) and str(u.get("telegramId")) == str(telegram_id)]
        if len(matched) != len(body):
            logger.warning(
                f"Remnawave stream?telegramId={telegram_id} returned "
                f"{len(body) - len(matched)} users with another telegramId, dropped"
            )
        return matched

    async def get_user_by_telegram_id(self, telegram_id: int, strict: bool = False) -> Optional[RemnaUser]:
        """The primary panel user of a Telegram id (ACTIVE, latest expireAt).

        strict=False (2.x UI reads): any error -> None.
        strict=True (before creating a user): None only for 404 / empty list;
        a panel error is raised so "panel down" is never read as "no user".
        """
        try:
            users = await self.find_users_by_telegram_id(telegram_id)
        except Exception as e:
            if is_not_found_error(e):
                return None
            logger.error(f"get_user_by_telegram_id({telegram_id}) failed: {type(e).__name__}")
            if strict:
                raise
            return None
        if not users:
            return None
        if len(users) > 1:
            logger.warning(
                f"Several Remnawave users with telegramId={telegram_id}: "
                f"{[u.get('id') for u in users]}, picking ACTIVE with the latest expireAt"
            )
        user_data = pick_primary_remna_user(users)
        uid = (user_data or {}).get("id") or (user_data or {}).get("uuid")
        if not uid:
            return None
        return RemnaUser(
            uuid=str(uid),
            telegram_id=telegram_id,
            username=user_data.get("username"),
            name=user_data.get("description") or user_data.get("username"),
            raw_data=user_data,
        )

    async def get_user_with_subscription_by_telegram_id(
        self, telegram_id: int
    ) -> Optional[tuple[RemnaUser, Optional[RemnaSubscription]]]:
        """(RemnaUser, RemnaSubscription | None). Active = ACTIVE/LIMITED and
        expireAt in the future (06 L6). A date before 2020 = no subscription."""
        remna_user = await self.get_user_by_telegram_id(telegram_id)
        if not remna_user:
            return None
        raw = remna_user.raw_data
        expire = parse_dt(raw.get("expireAt"))
        if expire is None or expire.year < SENTINEL_YEAR_BEFORE:
            return (remna_user, None)
        now = datetime.now(timezone.utc)
        status = str(raw.get("status") or "").upper()
        # Older payloads without status: treat as ACTIVE (2.x behaviour for the date check).
        is_active = (status in LIVE_STATUSES or not status) and expire > now
        squads = [s.get("name") for s in raw.get("activeInternalSquads") or [] if isinstance(s, dict)]
        return (
            remna_user,
            RemnaSubscription(
                active=is_active,
                expires_at=expire.replace(tzinfo=None),
                plan=plan_from_squad_names(squads),
                raw_data=raw,
            ),
        )

    async def update_user(self, user_id: str, **kwargs) -> Dict[str, Any]:
        """PATCH /api/users with the numeric id in the body (3.x)."""
        payload = build_user_payload_from_kwargs(kwargs)
        payload["id"] = panel_id(user_id)
        if len(payload) == 1:
            logger.debug("Remna update_user: nothing to update")
            return {}
        return await self.request("PATCH", "/api/users", json=payload)

    # ----------------------------------------------------------------- squads

    async def get_nodes(self) -> Dict[str, Any]:
        return await self.request("GET", "/api/nodes")

    async def get_internal_squads(self) -> Dict[str, Any]:
        return await self.request("GET", "/api/internal-squads")

    async def list_internal_squads(self) -> list:
        """Internal squads as dicts. Errors are raised (unlike get_squad_by_name)."""
        response = await self.get_internal_squads()
        body = unwrap(response)
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            squads = body.get("internalSquads", body.get("items"))
            if squads is not None:
                return list(squads)
        return []

    async def get_squad_by_name(self, squad_name: str) -> Optional[Dict[str, Any]]:
        """Squad by name, None when absent or on error (2.x callers)."""
        try:
            for squad in await self.list_internal_squads():
                if isinstance(squad, dict) and squad.get("name") == squad_name:
                    return squad
            return None
        except Exception as e:
            logger.error(f"Remna squad lookup {squad_name!r} failed: {type(e).__name__}")
            return None

    # ------------------------------------------------------------------- hwid

    async def get_hwid_devices(self, user_id: Any) -> Dict[str, Any]:
        """GET /api/hwid/devices/{userId} -> {"response": {"total", "devices"}}."""
        return await self.request("GET", f"/api/hwid/devices/{panel_id(user_id)}")

    async def delete_hwid_device(self, user_id: Any, hwid: str) -> Dict[str, Any]:
        """POST /api/hwid/devices/delete {userId, hwid}; returns the remaining devices."""
        return await self.request(
            "POST", "/api/hwid/devices/delete", json={"userId": panel_id(user_id), "hwid": str(hwid)}
        )

    async def list_all_hwid_devices(self, size: int = 500, start: int = 0) -> Dict[str, Any]:
        """GET /api/hwid/devices page over every user (offset ``start`` from 0)."""
        size = max(1, min(int(size), 1000))
        return await self.request("GET", f"/api/hwid/devices?size={size}&start={max(0, int(start))}")

    # ------------------------------------------------------------ subscription

    async def get_user_subscription_url(self, user_id: str) -> Optional[str]:
        """``response.subscriptionUrl`` with the SUBSCRIPTION_BASE_URL host
        override. A bare ``subscriptionToken`` is turned into a URL on that base.
        UI read: None on any error. The URL is never logged."""
        try:
            raw = unwrap(await self.get_user_by_id(user_id))
        except Exception as e:
            logger.error(f"Remna subscription URL for {user_id}: {type(e).__name__}")
            return None
        if not isinstance(raw, dict):
            return None
        url = raw.get("subscriptionUrl")
        if url:
            return apply_subscription_domain(url)
        token = raw.get("subscriptionToken")
        base = _subscription_base()
        if token and base:
            return f"{base}/{token}"
        logger.warning(f"Remna: no subscription URL for user {user_id}")
        return None

    async def health_check(self) -> Dict[str, Any]:
        return await self.request("GET", "/api/system/health")
