"""Promo engine of release 3.0 (stream E). Implements ports.PromoService.

One table-driven engine for every "free access" path:

  - built-in codes (BUILTIN_PROMOS): /trial (5 days, writes a ``trials`` row),
    /solokhin, /sun718 (referral rules, see ``_sun718``);
  - promo codes from ``promo_codes`` (PROMO_CODES_ENABLED): rewards in days,
    traffic and devices, audience new | existing | any, max_uses,
    per_user_limit, valid_from/valid_until;
  - gifts ``g_<token>`` (GIFTS_ENABLED): a one-time ``promo_codes`` row of
    kind "gift" made by ``create_gift`` after a gift purchase (stream A).

Every redemption runs under the per-user Redis lock ``lock:promo:<id>`` and
is RECORD-FIRST: the fact of use is written to the database before access
is granted, and rolled back if the grant fails. The record is the second
line against races (unique ``payments.external_id`` promo_<code>_<id> and
``trials.telegram_user_id`` for built-ins, ``SELECT ... FOR UPDATE`` on the
code row for table codes), so a Redis outage never makes a code reusable.

Access itself is granted only through ProvisioningService.grant (stream B).
No aiogram here; admin messages go through the Notifier port.
"""
from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.domain.models import (
    AdminTopic,
    Entitlement,
    EntitlementSource,
    PromoOutcome,
    PromoReward,
    SubscriptionState,
    ensure_utc,
)
from app.domain.texts import fmt_date_msk, h
from app.logger import logger
from app.services.promo_types import (  # noqa: F401 - re-exported
    PromoCodeRow,
    PromoCodeSpec,
    PromoRepo,
    RecordResult,
    ReserveResult,
)

GIB = 1024 ** 3

# Payments that are not money from a customer (stats, audience "existing").
NON_REVENUE_PROVIDERS = ("promo", "test", "referral_payout", "admin")

AUDIENCE_NEW = "new"
AUDIENCE_EXISTING = "existing"
AUDIENCE_ANY = "any"
AUDIENCES = (AUDIENCE_NEW, AUDIENCE_EXISTING, AUDIENCE_ANY)
# promo_codes.audience comment in r30_01 says all|new|paid|expired.
_AUDIENCE_ALIASES = {"all": AUDIENCE_ANY, "paid": AUDIENCE_EXISTING}

KIND_DAYS = "days"
KIND_PLAN = "plan"
KIND_GIFT = "gift"
KIND_TRIAL = "trial"
KIND_DISCOUNT = "discount"  # stored only; checkout (stream A) owns discounts

CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
GIFT_PREFIX = "g_"
_TOKEN_ALPHABET = string.ascii_lowercase + string.digits

# Gift months -> days (Entitlement carries days; a month table keeps 12 months = a year).
MONTH_DAYS = {1: 30, 3: 91, 6: 183, 12: 365}


def months_to_days(months: int) -> int:
    return MONTH_DAYS.get(int(months), int(months) * 30)


def normalize_code(code: Optional[str]) -> str:
    return (code or "").strip().lstrip("/").strip().lower()


def normalize_audience(value: Optional[str]) -> Optional[str]:
    v = (value or AUDIENCE_ANY).strip().lower()
    v = _AUDIENCE_ALIASES.get(v, v)
    return v if v in AUDIENCES else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _naive(dt: datetime) -> datetime:
    return ensure_utc(dt).replace(tzinfo=None)


@dataclass(frozen=True)
class BuiltinPromo:
    code: str
    plan_code: str
    days: int
    flag: str  # Settings attribute that switches the code on/off
    trial: bool = False
    requires_no_active: bool = True
    tariff: str = ""  # 2.x tariff name kept in payment metadata


BUILTIN_PROMOS: dict[str, BuiltinPromo] = {
    "trial": BuiltinPromo("trial", "standard", 5, "PROMO_TRIAL_ENABLED", trial=True, tariff="trial_standard_5d"),
    "solokhin": BuiltinPromo("solokhin", "premium", 15, "PROMO_SOLOKHIN_ENABLED", tariff="solokhin_15d"),
    "sun718": BuiltinPromo("sun718", "pro", 5, "PROMO_SUN718_ENABLED", requires_no_active=False, tariff="sun718_5d"),
}


def builtin_external_id(code: str, telegram_id: int) -> str:
    """payments.external_id of a built-in promo use (unique; 2.x format)."""
    return f"promo_{code}_{int(telegram_id)}"


# --------------------------------------------------------------------------- engine


class PromoEngine:
    """ports.PromoService + admin API for codes + create_gift for stream A."""

    def __init__(
        self,
        *,
        provisioning: Any,
        status: Any,
        notifier: Any,
        repo: Optional[PromoRepo] = None,
        settings: Any = None,
    ):
        self.provisioning = provisioning
        self.status = status
        self.notifier = notifier
        if repo is None:
            from app.services.promo_repo import SqlPromoRepo

            repo = SqlPromoRepo()
        self.repo: PromoRepo = repo
        self._settings = settings

    @property
    def settings(self):
        if self._settings is not None:
            return self._settings
        from app.config import settings

        return settings

    def _flag(self, name: str, default: bool = False) -> bool:
        return bool(getattr(self.settings, name, default))

    def _support(self) -> Optional[str]:
        return getattr(self.settings, "SUPPORT_HANDLE", None) or getattr(self.settings, "ADMIN_SUPPORT_USERNAME", None)

    # ------------------------------------------------------------ port API

    async def start_trial(self, telegram_id: int) -> PromoReward:
        return await self.redeem(telegram_id, "trial", source="trial")

    async def trial_available(self, telegram_id: int) -> bool:
        if not self._flag("PROMO_TRIAL_ENABLED", True):
            return False
        try:
            if await self.repo.trial_used(int(telegram_id)):
                return False
            state = await self.status.get_state(int(telegram_id))
            return not state.active
        except Exception as e:  # noqa: BLE001 - a menu CTA must not break the menu
            logger.warning(f"promo.trial_available tg={telegram_id} failed ({type(e).__name__})")
            return False

    async def redeem(self, telegram_id: int, code: str, *, source: str = "command") -> PromoReward:
        from app.infra.redis.locks import user_action_lock

        tg = int(telegram_id)
        code_n = normalize_code(code)
        if not CODE_RE.match(code_n):
            return PromoReward(code=code_n or "?", outcome=PromoOutcome.NOT_FOUND)
        async with user_action_lock("promo", tg) as acquired:
            if not acquired:
                return PromoReward(code=code_n, outcome=PromoOutcome.BUSY)
            try:
                if code_n in BUILTIN_PROMOS:
                    return await self._builtin(tg, BUILTIN_PROMOS[code_n], source)
                if code_n.startswith(GIFT_PREFIX):
                    return await self._gift(tg, code_n, source)
                return await self._table(tg, code_n, source)
            except Exception as e:  # noqa: BLE001 - never leak to the user
                logger.exception(f"promo.redeem code={code_n} tg={tg} failed: {type(e).__name__}")
                return PromoReward(code=code_n, outcome=PromoOutcome.ERROR)

    # ------------------------------------------------------------ helpers for routers

    async def is_known_code(self, code: str) -> bool:
        """For the /start <code> deep link: True when this router should take it."""
        code_n = normalize_code(code)
        if not CODE_RE.match(code_n):
            return False
        if code_n in BUILTIN_PROMOS:
            return self._flag(BUILTIN_PROMOS[code_n].flag, True)
        if code_n.startswith(GIFT_PREFIX):
            return self._flag("GIFTS_ENABLED")
        if not self._flag("PROMO_CODES_ENABLED"):
            return False
        try:
            row = await self.repo.get_code(code_n)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"promo.is_known_code failed ({type(e).__name__})")
            return False
        return row is not None and row.kind != KIND_GIFT

    # ------------------------------------------------------------ admin API

    async def create_code(self, spec: PromoCodeSpec, *, created_by: Optional[int] = None) -> Optional[PromoCodeRow]:
        code = normalize_code(spec.code)
        if not CODE_RE.match(code) or code in BUILTIN_PROMOS or code.startswith(GIFT_PREFIX):
            raise ValueError("bad code")
        if spec.kind not in (KIND_DAYS, KIND_PLAN, KIND_GIFT):
            raise ValueError("bad kind")
        if not spec.days or spec.days <= 0:
            raise ValueError("days must be positive")
        if normalize_audience(spec.audience) is None:
            raise ValueError("bad audience")
        if spec.plan_code:
            from app.domain.plans import is_valid_plan_code

            if not is_valid_plan_code(spec.plan_code):
                raise ValueError("bad plan")
        if spec.kind == KIND_PLAN and not spec.plan_code:
            raise ValueError("plan code required")
        from dataclasses import replace

        spec = replace(spec, code=code, audience=normalize_audience(spec.audience))
        return await self.repo.create_code(spec, created_by)

    async def list_codes(self, limit: int = 20) -> list[PromoCodeRow]:
        return await self.repo.list_codes(limit)

    async def get_code(self, code_id: int) -> Optional[PromoCodeRow]:
        return await self.repo.get_code_by_id(int(code_id))

    async def set_active(self, code_id: int, active: bool) -> bool:
        return await self.repo.set_active(int(code_id), bool(active))

    async def create_gift(self, buyer_telegram_id: int, plan_code: str, months: int, *,
                          payment_id: Optional[int] = None) -> str:
        """One-time gift code (stream A calls this after a paid gift purchase).

        Idempotent per payment_id: a webhook retry returns the same code.
        The deep link is ``https://t.me/<bot>?start=<code>`` (code starts with g_).
        """
        from app.domain.plans import PLAN_CATALOG

        plan = (plan_code or "").lower().strip()
        meta = PLAN_CATALOG.get(plan)
        if not meta or int(months) not in (meta.get("prices") or {}):
            raise ValueError("gift plan/period is not sellable")
        if payment_id is not None:
            existing = await self.repo.find_gift_by_payment(int(payment_id))
            if existing:
                return existing
        for _ in range(5):
            code = GIFT_PREFIX + "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(12))
            spec = PromoCodeSpec(
                code=code, kind=KIND_GIFT, plan_code=plan, days=months_to_days(int(months)),
                audience=AUDIENCE_ANY, max_uses=1, per_user_limit=1,
                meta={"buyer": int(buyer_telegram_id), "months": int(months),
                      "payment_id": str(payment_id) if payment_id is not None else None},
            )
            row = await self.repo.create_code(spec, int(buyer_telegram_id))
            if row is not None:
                return row.code
        raise RuntimeError("could not allocate a gift code")

    # ------------------------------------------------------------ built-ins

    async def _state(self, tg: int) -> SubscriptionState:
        return await self.status.get_state(tg, force=True)

    async def _grant(self, tg: int, ent: Entitlement, trace_id: str) -> SubscriptionState:
        state = await self.provisioning.grant(tg, ent, trace_id=trace_id)
        try:
            await self.status.invalidate(tg)
        except Exception:  # noqa: BLE001 - cache only
            pass
        return state

    async def _builtin(self, tg: int, promo: BuiltinPromo, source: str) -> PromoReward:
        code = promo.code
        if not self._flag(promo.flag, True):
            return PromoReward(code=code, outcome=PromoOutcome.DISABLED)
        if code == "sun718":
            from app.services.referral import redeem_sun718

            return await redeem_sun718(self, tg, promo)
        await self.repo.ensure_user(tg)
        used = await self.repo.trial_used(tg) if promo.trial else await self.repo.builtin_used(code, tg)
        if used:
            return PromoReward(code=code, outcome=PromoOutcome.ALREADY_USED)
        state = await self._state(tg)
        if state.stale:
            return PromoReward(code=code, outcome=PromoOutcome.ERROR)
        if promo.requires_no_active and state.active:
            return PromoReward(code=code, outcome=PromoOutcome.NOT_ELIGIBLE)
        meta = {"promo_code": code, "tariff": promo.tariff, "auto": True, "source": source}
        rec = await self.repo.record_builtin(code, tg, meta, trial=(promo.plan_code, promo.days) if promo.trial else None)
        if rec.status == "used":
            return PromoReward(code=code, outcome=PromoOutcome.ALREADY_USED)
        if rec.status != "ok":
            return PromoReward(code=code, outcome=PromoOutcome.ERROR)
        ent = Entitlement(
            plan_code=promo.plan_code,
            source=EntitlementSource.TRIAL if promo.trial else EntitlementSource.PROMO,
            days=promo.days,
            note=f"promo:{code}",
        )
        try:
            new_state = await self._grant(tg, ent, trace_id=f"promo:{code}:{tg}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"promo {code}: grant failed tg={tg} ({type(e).__name__}), rolling back the record")
            await self.repo.rollback_builtin(code, tg)
            await self._alert(f"❌ <b>{h(code.upper())}: выдача не удалась</b>", tg,
                              "Запись использования откатили, пользователь может повторить.")
            return PromoReward(code=code, outcome=PromoOutcome.ERROR)
        await self.repo.finish(rec.redemption_id, True, {"plan": promo.plan_code, "days": promo.days})
        await self._alert(
            f"🎁 <b>Промокод {h(code.upper())} активирован</b>", tg,
            f"📦 {h(promo.plan_code)} на {promo.days} дн.\n📅 До: {fmt_date_msk(new_state.expires_at)}",
        )
        return PromoReward(code=code, outcome=PromoOutcome.APPLIED, plan_code=promo.plan_code,
                           days=promo.days, expires_at=new_state.expires_at, redemption_id=rec.redemption_id)

    # ------------------------------------------------------------ table codes and gifts

    async def _audience_ok(self, tg: int, audience: str, state: SubscriptionState) -> bool:
        audience = normalize_audience(audience) or AUDIENCE_ANY
        if audience == AUDIENCE_ANY:
            return True
        existing = bool(state.active) or await self.repo.has_paid(tg)
        return existing if audience == AUDIENCE_EXISTING else not existing

    async def _table(self, tg: int, code: str, source: str) -> PromoReward:
        if not self._flag("PROMO_CODES_ENABLED"):
            return PromoReward(code=code, outcome=PromoOutcome.DISABLED)
        row = await self.repo.get_code(code)
        if row is None or row.kind == KIND_GIFT:
            return PromoReward(code=code, outcome=PromoOutcome.NOT_FOUND)
        if not row.is_active or row.kind not in (KIND_DAYS, KIND_PLAN):
            return PromoReward(code=code, outcome=PromoOutcome.DISABLED)
        now = _utcnow()
        if row.valid_from and ensure_utc(row.valid_from) > now:
            return PromoReward(code=code, outcome=PromoOutcome.NOT_FOUND)
        if row.valid_until and ensure_utc(row.valid_until) <= now:
            return PromoReward(code=code, outcome=PromoOutcome.EXPIRED)
        if row.max_uses is not None and row.uses >= row.max_uses:
            return PromoReward(code=code, outcome=PromoOutcome.EXHAUSTED)
        await self.repo.ensure_user(tg)
        state = await self._state(tg)
        if state.stale:
            return PromoReward(code=code, outcome=PromoOutcome.ERROR)
        if not await self._audience_ok(tg, row.audience, state):
            return PromoReward(code=code, outcome=PromoOutcome.NOT_ELIGIBLE)
        plan = row.plan_code if (row.kind == KIND_PLAN or not state.active) else (state.plan_code or row.plan_code)
        plan = (plan or "standard").lower()
        return await self._reserve_and_grant(tg, row, plan, EntitlementSource.PROMO, source)

    async def _gift(self, tg: int, code: str, source: str) -> PromoReward:
        if not self._flag("GIFTS_ENABLED"):
            return PromoReward(code=code, outcome=PromoOutcome.DISABLED)
        row = await self.repo.get_code(code)
        if row is None or row.kind != KIND_GIFT:
            return PromoReward(code=code, outcome=PromoOutcome.NOT_FOUND)
        if row.max_uses is not None and row.uses >= row.max_uses:
            return PromoReward(code=code, outcome=PromoOutcome.ALREADY_USED)
        await self.repo.ensure_user(tg)
        reward = await self._reserve_and_grant(tg, row, (row.plan_code or "standard").lower(),
                                               EntitlementSource.GIFT, source)
        if reward.outcome is PromoOutcome.EXHAUSTED:
            return PromoReward(code=code, outcome=PromoOutcome.ALREADY_USED)
        buyer = (row.meta or {}).get("buyer")
        if reward.applied and buyer and int(buyer) != tg:
            try:
                await self.notifier.notify_user(int(buyer), "🎁 Твой подарок активирован. Спасибо!",
                                                dedup_key=f"gift_used:{row.id}")
            except Exception:  # noqa: BLE001
                pass
        return reward

    async def _reserve_and_grant(self, tg: int, row: PromoCodeRow, plan: str,
                                 source_kind: EntitlementSource, source: str) -> PromoReward:
        code = row.code
        res = await self.repo.reserve(row.id, tg)
        mapping = {
            "not_found": PromoOutcome.NOT_FOUND,
            "exhausted": PromoOutcome.EXHAUSTED,
            "already_used": PromoOutcome.ALREADY_USED,
        }
        if res.status != "ok":
            return PromoReward(code=code, outcome=mapping.get(res.status, PromoOutcome.ERROR))
        ent = Entitlement(
            plan_code=plan,
            source=source_kind,
            days=int(row.days or 0),
            device_limit=row.devices or None,
            traffic_limit_bytes=(int(row.traffic_gb) * GIB) if row.traffic_gb else None,
            note=f"promo:{code}",
        )
        try:
            new_state = await self._grant(tg, ent, trace_id=f"promo:{code}:{tg}:{res.redemption_id}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"promo {code}: grant failed tg={tg} ({type(e).__name__}), releasing the reservation")
            await self.repo.finish(res.redemption_id, False)
            return PromoReward(code=code, outcome=PromoOutcome.ERROR)
        await self.repo.finish(res.redemption_id, True, {"plan": plan, "days": row.days, "traffic_gb": row.traffic_gb,
                                                          "devices": row.devices, "source": source})
        title = "🎁 <b>Подарок активирован</b>" if row.kind == KIND_GIFT else f"🎟 <b>Промокод {h(code)} активирован</b>"
        await self._alert(title, tg, f"📦 {h(plan)} +{row.days} дн.\n📅 До: {fmt_date_msk(new_state.expires_at)}"
                                     f"\nИспользований: {row.uses + 1}{'/' + str(row.max_uses) if row.max_uses else ''}")
        return PromoReward(code=code, outcome=PromoOutcome.APPLIED, plan_code=plan, days=row.days,
                           expires_at=new_state.expires_at, redemption_id=res.redemption_id)

    async def _alert(self, title_html: str, tg: int, body_html: str) -> None:
        text = f"{title_html}\n\n🆔 <code>{int(tg)}</code>\n{body_html}"
        try:
            await self.notifier.notify_admins(AdminTopic.PROMO, text, html=True)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"promo alert failed ({type(e).__name__})")


# --------------------------------------------------------------------------- wiring


_engine_cache: dict[int, PromoEngine] = {}


def get_promo(container: Any) -> PromoEngine:
    """The promo engine for this container.

    Until app.container wires PromoEngine as ``promo`` (request E.md), the
    port holds the Foundation placeholder; routers then get an engine built
    over the container's ports (one per container).
    """
    promo = getattr(container, "promo", None)
    if isinstance(promo, PromoEngine):
        return promo
    key = id(container)
    eng = _engine_cache.get(key)
    if eng is None or eng.provisioning is not container.provisioning or eng.notifier is not container.notifier:
        eng = PromoEngine(provisioning=container.provisioning, status=container.status,
                          notifier=container.notifier, settings=getattr(container, "settings", None))
        _engine_cache.clear()
        _engine_cache[key] = eng
    return eng


__all__ = [
    "PromoEngine", "PromoRepo", "PromoCodeSpec", "PromoCodeRow", "RecordResult", "ReserveResult",
    "BUILTIN_PROMOS", "BuiltinPromo", "AUDIENCES", "AUDIENCE_NEW", "AUDIENCE_EXISTING", "AUDIENCE_ANY",
    "KIND_DAYS", "KIND_PLAN", "KIND_GIFT", "GIFT_PREFIX", "NON_REVENUE_PROVIDERS", "normalize_code",
    "normalize_audience", "months_to_days", "builtin_external_id", "get_promo",
]
