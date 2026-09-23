"""2.x surface of the Remnawave client (stream B), kept for the 2.x services
that still use it (retire in 3.0.1).

The 2.x modules (sync_service, users, obhod_service, remna_service, yookassa)
still call these methods on RemnaClient; 3.0 code uses
app.infra.remnawave.gateway.HttpRemnaGateway. RemnaClient mixes this class in,
so ``patch.object(RemnaClient, ...)`` and the old import path keep working.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional, Union

from app.infra.remnawave.dto import LIVE_STATUSES, SENTINEL_YEAR_BEFORE, parse_dt, unwrap
from app.infra.remnawave.util import is_not_found_error, plan_from_squad_names
from app.logger import logger


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


class LegacyClientMixin:
    """Methods only 2.x code calls. ``self`` is a RemnaClient."""

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

    async def get_nodes(self) -> Dict[str, Any]:
        return await self.request("GET", "/api/nodes")

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
