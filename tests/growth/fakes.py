"""In-memory fakes for stream E tests: promo repo, redemption ledger,
provisioning (idempotent per trace_id, like the ProvisioningService contract)
and a status service. Every repo method is atomic (no await between check
and write), which is what the SQL repo gets from unique keys / FOR UPDATE.
"""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.domain.models import Entitlement, SubscriptionState
from app.services.promo import (
    PromoCodeRow,
    PromoCodeSpec,
    RecordResult,
    ReserveResult,
    builtin_external_id,
    normalize_code,
)


class MemoryPromoRepo:
    def __init__(self):
        self.users: set[int] = set()
        self.payments: dict[str, dict] = {}  # external_id -> row
        self.trials: dict[int, dict] = {}
        self.redemptions: dict[int, dict] = {}
        self.codes: dict[int, PromoCodeRow] = {}
        self.paid: dict[int, str] = {}  # tg -> last paid plan (real payments)
        self._ids = itertools.count(1)
        self.fail_record = False

    async def ensure_user(self, telegram_id: int) -> None:
        self.users.add(int(telegram_id))

    async def builtin_used(self, code: str, telegram_id: int) -> bool:
        return builtin_external_id(code, telegram_id) in self.payments

    async def trial_used(self, telegram_id: int) -> bool:
        return await self.builtin_used("trial", telegram_id) or int(telegram_id) in self.trials

    async def record_builtin(self, code, telegram_id, meta, *, trial=None) -> RecordResult:
        await asyncio.sleep(0)  # a real DB round trip yields to other tasks
        if self.fail_record:
            return RecordResult("error")
        ext = builtin_external_id(code, telegram_id)
        if ext in self.payments or (trial and int(telegram_id) in self.trials):
            return RecordResult("used")
        pid, rid = next(self._ids), next(self._ids)
        self.payments[ext] = {"id": pid, "tg": int(telegram_id), "meta": dict(meta)}
        self.redemptions[rid] = {"code": code, "tg": int(telegram_id), "payment_id": pid, "status": "pending",
                                 "promo_code_id": None}
        if trial:
            self.trials[int(telegram_id)] = {"plan": trial[0], "days": trial[1], "payment_id": pid}
        return RecordResult("ok", payment_id=pid, redemption_id=rid)

    async def rollback_builtin(self, code, telegram_id) -> None:
        row = self.payments.pop(builtin_external_id(code, telegram_id), None)
        if row:
            self.trials = {k: v for k, v in self.trials.items() if v["payment_id"] != row["id"]}
            self.redemptions = {k: v for k, v in self.redemptions.items() if v["payment_id"] != row["id"]}

    async def finish(self, redemption_id, ok, reward=None) -> None:
        r = self.redemptions.get(redemption_id)
        if r is None:
            return
        r["status"] = "applied" if ok else "failed"
        r["reward"] = reward
        if not ok and r["promo_code_id"] is not None:
            row = self.codes[r["promo_code_id"]]
            self.codes[row.id] = replace(row, uses=max(0, row.uses - 1))

    async def has_paid(self, telegram_id) -> bool:
        return int(telegram_id) in self.paid

    async def last_paid_plan(self, telegram_id) -> Optional[str]:
        return self.paid.get(int(telegram_id))

    async def get_code(self, code) -> Optional[PromoCodeRow]:
        code = normalize_code(code)
        return next((r for r in self.codes.values() if r.code == code), None)

    async def get_code_by_id(self, code_id) -> Optional[PromoCodeRow]:
        return self.codes.get(int(code_id))

    async def reserve(self, code_id, telegram_id) -> ReserveResult:
        await asyncio.sleep(0)
        row = self.codes.get(int(code_id))
        if row is None:
            return ReserveResult("not_found")
        if row.max_uses is not None and row.uses >= row.max_uses:
            return ReserveResult("exhausted")
        mine = sum(1 for r in self.redemptions.values()
                   if r["promo_code_id"] == row.id and r["tg"] == int(telegram_id) and r["status"] in ("pending", "applied"))
        if mine >= row.per_user_limit:
            return ReserveResult("already_used")
        rid = next(self._ids)
        self.redemptions[rid] = {"code": row.code, "tg": int(telegram_id), "payment_id": None, "status": "pending",
                                 "promo_code_id": row.id}
        self.codes[row.id] = replace(row, uses=row.uses + 1)
        return ReserveResult("ok", redemption_id=rid)

    async def create_code(self, spec: PromoCodeSpec, created_by) -> Optional[PromoCodeRow]:
        code = normalize_code(spec.code)
        if any(r.code == code for r in self.codes.values()):
            return None
        cid = next(self._ids)
        row = PromoCodeRow(id=cid, code=code, kind=spec.kind, plan_code=spec.plan_code, days=spec.days,
                           traffic_gb=spec.traffic_gb, devices=spec.devices, audience=spec.audience,
                           max_uses=spec.max_uses, per_user_limit=spec.per_user_limit, valid_from=spec.valid_from,
                           valid_until=spec.valid_until, meta=dict(spec.meta or {}), created_by=created_by)
        self.codes[cid] = row
        return row

    async def list_codes(self, limit=20, *, include_gifts=False):
        rows = [r for r in self.codes.values() if include_gifts or r.kind != "gift"]
        return sorted(rows, key=lambda r: -r.id)[:limit]

    async def set_active(self, code_id, active) -> bool:
        row = self.codes.get(int(code_id))
        if row is None:
            return False
        self.codes[row.id] = replace(row, is_active=bool(active))
        return True

    async def find_gift_by_payment(self, payment_id) -> Optional[str]:
        for r in self.codes.values():
            if r.kind == "gift" and (r.meta or {}).get("payment_id") == str(payment_id):
                return r.code
        return None

    # helpers
    def applied(self, code: Optional[str] = None) -> list[dict]:
        return [r for r in self.redemptions.values() if r["status"] == "applied" and (code is None or r["code"] == code)]


class MemoryLedger:
    def __init__(self):
        self.rows: dict[tuple[str, int], dict] = {}

    async def state(self, code, telegram_id):
        r = self.rows.get((code, int(telegram_id)))
        return r["status"] if r else None

    async def open(self, code, telegram_id, reward):
        self.rows[(code, int(telegram_id))] = {"status": "pending", "reward": dict(reward)}
        return len(self.rows)

    async def close(self, code, telegram_id, status, reward=None):
        r = self.rows.get((code, int(telegram_id)))
        if r:
            r["status"] = status

    async def count(self, code, status):
        return sum(1 for (c, _), r in self.rows.items() if c == code and r["status"] == status)


class FakeStatus:
    """StatusService: states set by the test; default no subscription."""

    def __init__(self):
        self.states: dict[int, SubscriptionState] = {}
        self.invalidated: list[int] = []

    def set(self, tg: int, **kw) -> None:
        self.states[int(tg)] = SubscriptionState(telegram_id=int(tg), **kw)

    async def get_state(self, telegram_id, *, force=False) -> SubscriptionState:
        return self.states.get(int(telegram_id)) or SubscriptionState(telegram_id=int(telegram_id))

    async def invalidate(self, telegram_id) -> None:
        self.invalidated.append(int(telegram_id))


class FakeProvisioning:
    """grant() idempotent per trace_id; extends from max(now, expiry); keeps
    the status fake in sync so later checks see the new state."""

    def __init__(self, status: Optional[FakeStatus] = None, *, delay: float = 0.0):
        self.status = status
        self.delay = delay
        self.calls: list[tuple[int, Entitlement, str]] = []
        self.applied: dict[str, SubscriptionState] = {}
        self.fail = False

    async def grant(self, telegram_id, entitlement: Entitlement, *, trace_id: str) -> SubscriptionState:
        self.calls.append((int(telegram_id), entitlement, trace_id))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("panel down")
        if trace_id in self.applied:
            return self.applied[trace_id]
        now = datetime.now(timezone.utc)
        cur = await self.status.get_state(telegram_id) if self.status else SubscriptionState(telegram_id=int(telegram_id))
        base = max(now, cur.expires_at) if (cur.active and cur.expires_at) else now
        if entitlement.is_lifetime:
            st = SubscriptionState(telegram_id=int(telegram_id), active=True, plan_code=entitlement.plan_code,
                                   is_lifetime=True, has_panel_user=True)
        else:
            st = SubscriptionState(telegram_id=int(telegram_id), active=True, plan_code=entitlement.plan_code,
                                   expires_at=base + timedelta(days=int(entitlement.days or 0)), has_panel_user=True)
        self.applied[trace_id] = st
        if self.status is not None:
            self.status.states[int(telegram_id)] = st
        return st

    async def revoke(self, telegram_id, *, sub_kind=None, reason="", trace_id="") -> bool:
        return True

    @property
    def effective(self) -> int:
        """Number of distinct grants actually applied."""
        return len(self.applied)


class Settings:
    """Minimal settings object for engines under test."""

    def __init__(self, **kw):
        self.PROMO_TRIAL_ENABLED = True
        self.PROMO_SOLOKHIN_ENABLED = True
        self.PROMO_SUN718_ENABLED = True
        self.PROMO_CODES_ENABLED = True
        self.GIFTS_ENABLED = True
        self.PROMO_SUN718_OWNER_TG_ID = 900000777
        self.SUPPORT_HANDLE = "support"
        self.ADMINS = [900000199]
        for k, v in kw.items():
            setattr(self, k, v)
