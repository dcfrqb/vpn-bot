"""Admin grants and day credits (stream E).

- ``add_days``: +N days to the user's current plan through
  ProvisioningService (``add_days`` when stream B ships it, else ``grant``
  with the current plan). Used by admin /grant and broadcast credit_days.
- ``RedemptionLedger``: record-first ledger on ``promo_redemptions``
  (one row per (code, user); pending -> applied | failed | skipped). Codes:
  ``adm:<request>`` for admin grants, ``bc:<broadcast_id>`` for broadcast credits.
- ``GrantsService``: admin decisions on /friend and /admin requests
  (Pro 1m / 3m / forever, reject) and /grant; a decision is taken once
  (Redis marker per request), under the per-user lock ``lock:grant:<id>``,
  recorded before the grant.
- ``ObhodAdmin``: obhod package control for admins over the 2.x obhod
  service (stream B replaces it with services/obhod.py).

No aiogram here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Optional, Protocol

from app.domain.models import AdminTopic, Entitlement, EntitlementSource, SubKind, SubscriptionState
from app.domain.texts import fmt_date_msk, h
from app.logger import logger

# key -> (plan, days or None for lifetime, label). The top plan (Pro) is the
# "friend" grant since the lite/standard/pro grid (2.x _FRIEND_GRANT_MAP).
GRANT_KEYS: dict[str, tuple[str, Optional[int], str]] = {
    "1m": ("pro", 30, "Pro на 1 месяц"),
    "3m": ("pro", 90, "Pro на 3 месяца"),
    "forever": ("pro", None, "Pro навсегда"),
}
REQUEST_MARKER_TTL = 7 * 24 * 3600
LEGACY_MARKER_TTL = 300  # 2.x buttons carry only the user id


async def add_days(
    provisioning: Any,
    status: Any,
    telegram_id: int,
    days: int,
    *,
    trace_id: str,
    source: EntitlementSource = EntitlementSource.ADMIN,
    plan_code: Optional[str] = None,
) -> Any:
    """+``days`` to the main subscription. None when there is nothing to
    extend (no subscription and no ``plan_code``, or a lifetime one).
    Otherwise the new SubscriptionState, on both paths (native
    ProvisioningService.add_days, or a grant on ``plan_code``).

    Idempotent per ``trace_id`` (ProvisioningService contract)."""
    tg = int(telegram_id)
    native = getattr(provisioning, "add_days", None)
    if native is not None and plan_code is None:
        # ProvisioningService.add_days(tg, days, *, trace_id, reason) returns the
        # new expiry or None. It has no ``source`` argument: passing one raised
        # TypeError on every broadcast credit (review money M-3).
        new_expiry = await native(tg, int(days), trace_id=trace_id, reason=f"{source.value}:{trace_id}")
        if new_expiry is None:
            return None
        # Callers read ``.expires_at``: a bare datetime crashed /grant after the
        # days were credited (review round 2, N-2).
        return await state_after_credit(status, tg, new_expiry)
    state = await status.get_state(tg, force=True)
    if state.is_lifetime:
        return None
    plan = (plan_code or state.plan_code or "").lower()
    if not plan:
        return None
    ent = Entitlement(plan_code=plan, source=source, sub_kind=SubKind.MAIN, days=int(days), note=f"add_days:{trace_id}")
    new_state = await provisioning.grant(tg, ent, trace_id=trace_id)
    try:
        await status.invalidate(tg)
    except Exception:  # noqa: BLE001
        pass
    return new_state


async def state_after_credit(status: Any, telegram_id: int, new_expiry: Optional[datetime]) -> SubscriptionState:
    """The state after a credit that already landed. Never raises: the days
    are on the panel, so a failed re-read must not turn into an error (the
    caller would report a failure and an admin retry would credit again)."""
    tg = int(telegram_id)
    fallback = SubscriptionState(telegram_id=tg, has_panel_user=True, active=True, expires_at=new_expiry,
                                 stale=True)
    if status is None:
        return fallback
    try:
        state = await status.get_state(tg, force=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"grants: state re-read after credit failed tg={tg} ({type(e).__name__})")
        return fallback
    if not isinstance(state, SubscriptionState):
        return fallback
    if new_expiry is not None and (state.expires_at is None or state.expires_at < new_expiry):
        state = replace(state, expires_at=new_expiry)  # a stale cache must not show the old date
    return state


async def credit_landed(trace_id: str) -> bool:
    """True when ProvisioningService.add_days kept its ``credit:<trace_id>``
    marker, i.e. the PATCH went through (a failed PATCH deletes it). Redis
    down -> False."""
    from app.infra.redis.flags import get_value

    return await get_value(f"credit:{trace_id}") is not None


# --------------------------------------------------------------------------- ledger


class RedemptionLedger(Protocol):
    async def state(self, code: str, telegram_id: int) -> Optional[str]: ...
    async def open(self, code: str, telegram_id: int, reward: dict) -> Optional[int]: ...
    async def close(self, code: str, telegram_id: int, status: str, reward: Optional[dict] = None) -> None: ...
    async def count(self, code: str, status: str) -> int: ...


class SqlRedemptionLedger:
    """RedemptionLedger over promo_redemptions (promo_code_id NULL)."""

    def __init__(self, session_factory: Any = None):
        self._factory = session_factory

    def _session(self):
        if self._factory is not None:
            return self._factory()
        from app.db.session import SessionLocal

        if SessionLocal is None:
            raise RuntimeError("database is not configured")
        return SessionLocal()

    async def _row(self, s, code: str, tg: int):
        from sqlalchemy import select

        from app.db.models import PromoRedemption

        r = await s.execute(select(PromoRedemption).where(
            PromoRedemption.code == code, PromoRedemption.telegram_user_id == int(tg),
            PromoRedemption.promo_code_id.is_(None)).order_by(PromoRedemption.id.desc()).limit(1))
        return r.scalar_one_or_none()

    async def state(self, code: str, telegram_id: int) -> Optional[str]:
        async with self._session() as s:
            row = await self._row(s, code, telegram_id)
            return row.status if row else None

    async def open(self, code: str, telegram_id: int, reward: dict) -> Optional[int]:
        from app.db.models import PromoRedemption

        async with self._session() as s:
            row = await self._row(s, code, telegram_id)
            if row is None:
                row = PromoRedemption(code=code, telegram_user_id=int(telegram_id), status="pending", reward=reward)
                s.add(row)
            else:
                row.status = "pending"
                row.reward = reward
            await s.flush()
            rid = row.id
            await s.commit()
            return rid

    async def close(self, code: str, telegram_id: int, status: str, reward: Optional[dict] = None) -> None:
        async with self._session() as s:
            row = await self._row(s, code, telegram_id)
            if row is None:
                return
            row.status = status
            if reward is not None:
                row.reward = {**(row.reward or {}), **reward}
            await s.commit()

    async def count(self, code: str, status: str) -> int:
        from sqlalchemy import func, select

        from app.db.models import PromoRedemption

        async with self._session() as s:
            r = await s.execute(select(func.count(PromoRedemption.id)).where(
                PromoRedemption.code == code, PromoRedemption.status == status))
            return int(r.scalar_one() or 0)


async def _ensure_user(telegram_id: int) -> None:
    try:
        from app.services.promo_repo import SqlPromoRepo

        await SqlPromoRepo().ensure_user(int(telegram_id))
    except Exception as e:  # noqa: BLE001 - FK guard is best effort
        logger.warning(f"grants: ensure_user tg={telegram_id} failed ({type(e).__name__})")


# --------------------------------------------------------------------------- admin grants


@dataclass(frozen=True)
class GrantResult:
    status: str  # ok | dup | busy | skipped | error
    label: str = ""
    state: Optional[SubscriptionState] = None


class GrantsService:
    def __init__(self, *, provisioning: Any, status: Any, notifier: Any,
                 ledger: Optional[RedemptionLedger] = None, ensure_user: Any = None):
        self.provisioning = provisioning
        self.status = status
        self.notifier = notifier
        self.ledger: RedemptionLedger = ledger or SqlRedemptionLedger()
        self._ensure_user = ensure_user or _ensure_user

    @staticmethod
    async def take_request(request_key: str, *, legacy: bool = False) -> bool:
        """First decision on a request wins (any admin, any button). Redis
        down -> True (the per-user lock and the ledger still protect)."""
        from app.infra.redis.flags import set_once

        got = await set_once(f"grant_req:{request_key}", "1",
                             ttl=LEGACY_MARKER_TTL if legacy else REQUEST_MARKER_TTL)
        return got is not False

    @staticmethod
    async def release_request(request_key: str) -> None:
        from app.infra.redis.flags import delete_key

        await delete_key(f"grant_req:{request_key}")

    async def grant_key(self, admin_id: int, telegram_id: int, key: str, *, request_key: str,
                        legacy: bool = False) -> GrantResult:
        """Pro 1m / 3m / forever for a /friend or /admin request."""
        if key not in GRANT_KEYS:
            return GrantResult("error")
        plan, days, label = GRANT_KEYS[key]
        if not await self.take_request(request_key, legacy=legacy):
            return GrantResult("dup", label)
        res = await self._grant(admin_id, int(telegram_id), plan=plan, days=days, label=label,
                                code=f"adm:{request_key}"[:64], trace_id=f"admin:{request_key}:{key}")
        if res.status in ("error", "busy"):
            await self.release_request(request_key)
        return res

    async def reject(self, request_key: str, *, legacy: bool = False) -> bool:
        return await self.take_request(request_key, legacy=legacy)

    async def grant_days(self, admin_id: int, telegram_id: int, days: int, *, plan: Optional[str] = None,
                         request_key: str) -> GrantResult:
        """/grant <id> <days> [plan]: +days to the current plan (or to ``plan``)."""
        if not await self.take_request(request_key):
            return GrantResult("dup")
        label = f"+{int(days)} дн." + (f" ({plan})" if plan else "")
        res = await self._grant(admin_id, int(telegram_id), plan=plan, days=int(days), label=label,
                                code=f"adm:{request_key}"[:64], trace_id=f"admin:{request_key}", extend=True)
        if res.status in ("error", "busy"):
            await self.release_request(request_key)
        return res

    async def _grant(self, admin_id: int, tg: int, *, plan: Optional[str], days: Optional[int], label: str,
                     code: str, trace_id: str, extend: bool = False) -> GrantResult:
        from app.infra.redis.locks import user_action_lock

        async with user_action_lock("grant", tg) as acquired:
            if not acquired:
                return GrantResult("busy", label)
            await self._ensure_user(tg)
            reward = {"admin_id": int(admin_id), "plan": plan, "days": days, "trace_id": trace_id}
            try:
                if await self.ledger.state(code, tg) == "applied":
                    return GrantResult("dup", label)
                await self.ledger.open(code, tg, reward)
            except Exception as e:  # noqa: BLE001 - no record, no grant
                logger.error(f"grants: ledger open failed tg={tg} ({type(e).__name__})")
                return GrantResult("error", label)
            try:
                if extend:
                    try:
                        state = await add_days(self.provisioning, self.status, tg, int(days or 0),
                                               trace_id=trace_id, plan_code=plan)
                    except Exception as e:
                        # Review round 2, N-2: an error after the PATCH (DB save,
                        # cache) must not read as "not granted": the request would
                        # be released and a retry would credit a second time.
                        if plan is not None or not await credit_landed(trace_id):
                            raise
                        logger.warning(f"grants: +{days}d tg={tg} landed despite {type(e).__name__}")
                        state = await state_after_credit(self.status, tg, None)
                    if state is None:
                        await self.ledger.close(code, tg, "skipped")
                        return GrantResult("skipped", label)
                else:
                    ent = Entitlement(plan_code=plan or "pro", source=EntitlementSource.ADMIN, days=days,
                                      is_lifetime=days is None, note=f"admin:{admin_id}")
                    state = await self.provisioning.grant(tg, ent, trace_id=trace_id)
                    try:
                        await self.status.invalidate(tg)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as e:  # noqa: BLE001
                logger.error(f"grants: grant failed tg={tg} ({type(e).__name__})")
                await self.ledger.close(code, tg, "failed")
                return GrantResult("error", label)
            try:
                await self.ledger.close(code, tg, "applied")
            except Exception as e:  # noqa: BLE001 - granted; the request marker still blocks a repeat
                logger.error(f"grants: ledger close failed after grant tg={tg} ({type(e).__name__})")
        try:
            await self.notifier.notify_admins(
                AdminTopic.PROMO,
                f"⭐ <b>Выдача администратором</b>\n\n🆔 <code>{tg}</code>\n📦 {h(label)}\n"
                f"📅 До: {'бессрочно' if days is None and not extend else fmt_date_msk(state.expires_at)}\n"
                f"👤 Админ: <code>{int(admin_id)}</code>",
                html=True,
            )
        except Exception:  # noqa: BLE001
            pass
        return GrantResult("ok", label, state)


# --------------------------------------------------------------------------- obhod


@dataclass(frozen=True)
class ObhodInfo:
    telegram_id: int
    exists: bool
    active: bool = False
    used_bytes: Optional[int] = None
    limit_bytes: Optional[int] = None
    expire_at: Optional[datetime] = None
    package: Optional[str] = None
    package_until: Optional[str] = None


class ObhodAdmin:
    """Admin control of the obhod (bypass) subscription over the 2.x service."""

    async def info(self, telegram_id: int) -> ObhodInfo:
        from app.db.session import SessionLocal
        from app.services.obhod_service import get_obhod_link_info, get_obhod_subscription

        tg = int(telegram_id)
        data = await get_obhod_link_info(tg)
        if data is None:
            return ObhodInfo(tg, exists=False)
        pkg = until = None
        if SessionLocal is not None:
            async with SessionLocal() as s:
                sub = await get_obhod_subscription(s, tg)
                cfg = (sub.config_data or {}) if sub else {}
                pkg, until = cfg.get("package"), cfg.get("package_until")
        return ObhodInfo(tg, exists=True, active=bool(data.get("active")), used_bytes=data.get("used_bytes"),
                         limit_bytes=data.get("limit_bytes"), expire_at=data.get("expire_at"),
                         package=pkg, package_until=until)

    async def apply_package(self, telegram_id: int, package_code: str, *, admin_id: int) -> bool:
        from app.db.session import SessionLocal
        from app.infra.redis.locks import user_action_lock
        from app.services.obhod_service import apply_obhod_package

        if SessionLocal is None:
            return False
        async with user_action_lock("obhod", int(telegram_id)) as acquired:
            if not acquired:
                return False
            async with SessionLocal() as s:
                return await apply_obhod_package(s, int(telegram_id), package_code,
                                                 trace_id=f"admin_obhod_{admin_id}")

    async def reset_base(self, telegram_id: int) -> bool:
        """Back to the base monthly cap (package removed)."""
        from app.db.session import SessionLocal
        from app.domain.plans import OBHOD_TRAFFIC_LIMIT_STRATEGY, obhod_base_limit_bytes
        from app.remnawave.client import RemnaClient
        from app.services.obhod_service import get_obhod_subscription

        if SessionLocal is None:
            return False
        async with SessionLocal() as s:
            sub = await get_obhod_subscription(s, int(telegram_id))
            if not sub or not sub.remna_user_id:
                return False
            client = RemnaClient()
            try:
                await client.update_user(sub.remna_user_id, traffic_limit_bytes=obhod_base_limit_bytes(),
                                         traffic_limit_strategy=OBHOD_TRAFFIC_LIMIT_STRATEGY)
            finally:
                await client.close()
            cfg = dict(sub.config_data or {})
            for k in ("package", "package_until", "package_limit_bytes"):
                cfg.pop(k, None)
            sub.config_data = cfg
            await s.commit()
            return True

    async def deactivate(self, telegram_id: int) -> bool:
        from app.db.session import SessionLocal
        from app.services.obhod_service import deactivate_obhod

        if SessionLocal is None:
            return False
        async with SessionLocal() as s:
            return await deactivate_obhod(s, int(telegram_id), trace_id="admin_obhod_off")

    async def overview(self) -> dict:
        """Counts for the admin obhod screen (sub_kind='obhod' rows)."""
        from sqlalchemy import func, select

        from app.db.models import Subscription
        from app.db.session import SessionLocal

        if SessionLocal is None:
            return {"active": 0, "total": 0}
        async with SessionLocal() as s:
            total = (await s.execute(select(func.count(Subscription.id)).where(
                Subscription.sub_kind == "obhod"))).scalar_one()
            active = (await s.execute(select(func.count(Subscription.id)).where(
                Subscription.sub_kind == "obhod", Subscription.active.is_(True)))).scalar_one()
        return {"active": int(active or 0), "total": int(total or 0)}


__all__ = ["GRANT_KEYS", "add_days", "state_after_credit", "credit_landed", "RedemptionLedger", "SqlRedemptionLedger", "GrantResult",
           "GrantsService", "ObhodInfo", "ObhodAdmin"]
