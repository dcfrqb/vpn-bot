"""StatusService: what the menu shows (stream B).

Panel first, DB second:
  - the main panel account (PanelAccounts.find_main, never creates one) gives
    active / expiry / status / squads / device limit / subscription URL;
  - the main DB row gives autorenew, grace and the plan when the squads do
    not name one; the obhod row + its panel user give the obhod quota.

Grace (stream C, row grace_state='active'): ``active=False`` (not paid
time), ``expires_at`` = the paid term (valid_until), ``grace_until`` set; the
panel's expireAt (= grace end) is never shown as the paid term.

Cache (Redis JSON, app.infra.redis.cache):
  - ``status:v1:<tg>``   fresh copy, STATUS_TTL_S; ``get_state(force=True)``
    (the «Обновить» button) skips it; ``invalidate`` drops it;
  - ``status:last:<tg>`` last good copy, STALE_TTL_S; used only when the panel
    is unreachable, returned with ``stale=True``.
Panel down and no copy -> a DB-only state with ``stale=True``.
The cache holds the subscription URLs (the same data the DB keeps in
config_data); they are never logged.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.domain.models import PanelUser, SubKind, SubscriptionState
from app.infra.redis import cache
from app.logger import logger
from app.services.accounts import AccountsRepo, PanelAccounts, SqlAccountsRepo, SubRow, numeric_panel_id

STATUS_TTL_S = 60
STALE_TTL_S = 24 * 3600
FRESH_KEY = "status:v1:{}"
LAST_KEY = "status:last:{}"
LIVE = ("ACTIVE", "LIMITED")
_DT_KEYS = ("expires_at", "grace_until", "fetched_at")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def state_to_json(state: SubscriptionState) -> dict:
    data = asdict(state)
    data["sub_kind"] = state.sub_kind.value
    for k in _DT_KEYS:
        v = data.get(k)
        data[k] = v.isoformat() if isinstance(v, datetime) else None
    return data


def state_from_json(data: dict) -> Optional[SubscriptionState]:
    try:
        d = dict(data)
        d["sub_kind"] = SubKind(d.get("sub_kind") or "main")
        for k in _DT_KEYS:
            if d.get(k):
                d[k] = datetime.fromisoformat(d[k])
        allowed = SubscriptionState.__dataclass_fields__.keys()
        return SubscriptionState(**{k: v for k, v in d.items() if k in allowed})
    except (TypeError, ValueError):
        return None


def plan_from_squads(squads) -> Optional[str]:
    from app.infra.remnawave.client import plan_from_squad_names

    return plan_from_squad_names(squads)


def _is_live(user: Optional[PanelUser], now: datetime) -> bool:
    return bool(
        user
        and (user.status or "").upper() in LIVE
        and user.expire_at is not None
        and user.expire_at > now
    )


def _sub_url(url: Optional[str]) -> Optional[str]:
    from app.infra.remnawave.client import apply_subscription_domain

    return apply_subscription_domain(url)


def build_state(
    telegram_id: int,
    *,
    user: Optional[PanelUser],
    main_row: Optional[SubRow],
    obhod_row: Optional[SubRow] = None,
    obhod_user: Optional[PanelUser] = None,
    devices_used: Optional[int] = None,
    now: Optional[datetime] = None,
    stale: bool = False,
) -> SubscriptionState:
    """Pure: panel user + DB rows -> SubscriptionState."""
    now = now or _now()
    # Grace (stream C): the panel shows grace_until as expireAt and the grace
    # squad. That is not paid time: show the paid term and grace_until apart.
    in_grace = bool(main_row and main_row.grace_state == "active")
    row_live = bool(main_row and main_row.active and (
        main_row.is_lifetime or (main_row.valid_until is not None and main_row.valid_until > now)))
    if user is not None:
        expires = user.expire_at if (user.expire_at and user.expire_at.year >= 2020) else None
        active = _is_live(user, now)
        plan = plan_from_squads(user.squads) or (main_row.plan_code if main_row else None) or None
        is_lifetime = bool(expires and expires.year >= 2099)
        device_limit = user.device_limit
        status = user.status
        url = _sub_url(user.subscription_url)
    else:
        expires = main_row.valid_until if main_row else None
        active = row_live if stale else False
        plan = (main_row.plan_code or None) if main_row else None
        is_lifetime = bool(main_row and main_row.is_lifetime)
        device_limit = None
        status = None
        url = None
    if in_grace:
        expires = main_row.valid_until
        active = False
        plan = (main_row.plan_code or None) or plan
        is_lifetime = False
    cfg = (main_row.config_data if main_row else None) or {}
    obhod_active = bool(obhod_row and obhod_row.active)
    if obhod_user is not None:
        obhod_active = obhod_active and _is_live(obhod_user, now)
    return SubscriptionState(
        telegram_id=int(telegram_id),
        has_panel_user=user is not None,
        active=active,
        plan_code=plan,
        sub_kind=SubKind.MAIN,
        expires_at=expires,
        is_lifetime=is_lifetime,
        device_limit=device_limit,
        devices_used=devices_used,
        panel_status=status,
        autorenew=bool(main_row and main_row.autorenew),
        grace_until=main_row.grace_until if in_grace else None,
        is_trial=bool(cfg.get("last_source") == "trial" or (plan == "trial")),
        obhod_active=obhod_active,
        obhod_used_bytes=obhod_user.used_traffic_bytes if obhod_user else None,
        obhod_limit_bytes=obhod_user.traffic_limit_bytes if obhod_user else None,
        subscription_url=url,
        obhod_subscription_url=_sub_url(obhod_user.subscription_url) if obhod_user else None,
        fetched_at=now,
        stale=stale,
    )


class PanelStatusService:
    """StatusService port (app.services.ports)."""

    def __init__(self, remna=None, repo: Optional[AccountsRepo] = None, *,
                 clock: Callable[[], datetime] = _now, count_devices: bool = True):
        if remna is None:
            from app.infra.remnawave.gateway import HttpRemnaGateway

            remna = HttpRemnaGateway()
        self.remna = remna
        self.repo = repo or SqlAccountsRepo()
        self.accounts = PanelAccounts(remna, self.repo)
        self.clock = clock
        self.count_devices = count_devices

    async def _rows(self, tg: int) -> tuple[Optional[SubRow], Optional[SubRow]]:
        try:
            return (await self.repo.get_subscription(tg, SubKind.MAIN),
                    await self.repo.get_subscription(tg, SubKind.OBHOD))
        except Exception as e:  # noqa: BLE001 - DB down: panel data is still useful
            logger.warning(f"status: DB read failed tg={tg} ({type(e).__name__})")
            return None, None

    async def _fetch(self, tg: int) -> SubscriptionState:
        main_row, obhod_row = await self._rows(tg)
        user = await self.accounts.find_main(tg)  # raises when the panel is down
        devices_used = None
        if user is not None and self.count_devices:
            try:
                devices_used = len(await self.remna.list_devices(user.id))
            except Exception as e:  # noqa: BLE001 - optional detail
                logger.debug(f"status: device count failed tg={tg} ({type(e).__name__})")
        obhod_user = None
        obhod_id = numeric_panel_id(obhod_row.remna_user_id) if obhod_row else None
        if obhod_id:
            try:
                obhod_user = await self.remna.get_user(obhod_id)
            except Exception as e:  # noqa: BLE001 - optional detail
                logger.debug(f"status: obhod read failed tg={tg} ({type(e).__name__})")
        return build_state(tg, user=user, main_row=main_row, obhod_row=obhod_row, obhod_user=obhod_user,
                           devices_used=devices_used, now=self.clock())

    async def get_state(self, telegram_id: int, *, force: bool = False) -> SubscriptionState:
        tg = int(telegram_id)
        if not force:
            cached = await cache.get_json(FRESH_KEY.format(tg))
            state = state_from_json(cached) if isinstance(cached, dict) else None
            if state is not None:
                return state
        try:
            state = await self._fetch(tg)
        except Exception as e:  # noqa: BLE001 - panel unreachable
            logger.warning(f"status: panel unreachable tg={tg} ({type(e).__name__}), serving stale data")
            return await self._stale(tg)
        data = state_to_json(state)
        await cache.set_json(FRESH_KEY.format(tg), data, STATUS_TTL_S)
        await cache.set_json(LAST_KEY.format(tg), data, STALE_TTL_S)
        return state

    async def _stale(self, tg: int) -> SubscriptionState:
        last = await cache.get_json(LAST_KEY.format(tg))
        state = state_from_json(last) if isinstance(last, dict) else None
        if state is not None:
            from dataclasses import replace

            return replace(state, stale=True)
        main_row, obhod_row = await self._rows(tg)
        return build_state(tg, user=None, main_row=main_row, obhod_row=obhod_row, now=self.clock(), stale=True)

    async def invalidate(self, telegram_id: int) -> None:
        tg = int(telegram_id)
        await cache.invalidate(FRESH_KEY.format(tg))
        # 2.x caches still read by site_profile and the 2.x reconciler (3.0.1)
        try:
            from app.services.cache import invalidate_subscription_cache, invalidate_sync_cache, invalidate_user_cache

            await invalidate_sync_cache(tg)
            await invalidate_user_cache(tg)
            await invalidate_subscription_cache(tg)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"status: legacy cache invalidate failed tg={tg} ({type(e).__name__})")


def state_summary(state: SubscriptionState) -> dict[str, Any]:
    """Log-safe summary (no URLs)."""
    return {"tg": state.telegram_id, "active": state.active, "plan": state.plan_code,
            "expires": state.expires_at.isoformat() if state.expires_at else None, "stale": state.stale}
