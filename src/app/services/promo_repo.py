"""SQL persistence of the promo engine (stream E): PromoRepo over SessionLocal.

Tables: payments (built-in promo rows, provider "promo", unique external_id
promo_<code>_<telegram_id>), promo_redemptions, promo_codes, trials.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from app.logger import logger
from app.services.promo import (
    AUDIENCE_ANY,
    KIND_GIFT,
    NON_REVENUE_PROVIDERS,
    PromoCodeRow,
    PromoCodeSpec,
    RecordResult,
    ReserveResult,
    _naive,
    builtin_external_id,
    normalize_audience,
    normalize_code,
)


def _row(pc: Any) -> PromoCodeRow:
    meta = dict(pc.meta or {})
    return PromoCodeRow(
        id=pc.id, code=pc.code, kind=pc.kind, plan_code=pc.plan_code, days=pc.days,
        traffic_gb=meta.get("traffic_gb"), devices=meta.get("devices"),
        audience=normalize_audience(pc.audience) or AUDIENCE_ANY, max_uses=pc.max_uses, uses=int(pc.uses or 0),
        per_user_limit=int(pc.per_user_limit or 1), valid_from=pc.valid_from, valid_until=pc.valid_until,
        is_active=bool(pc.is_active), meta=meta, created_by=pc.created_by, created_at=pc.created_at,
    )


class SqlPromoRepo:
    """PromoRepo over app.db.session.SessionLocal (PostgreSQL)."""

    def __init__(self, session_factory: Any = None):
        self._factory = session_factory

    def _session(self):
        if self._factory is not None:
            return self._factory()
        from app.db.session import SessionLocal

        if SessionLocal is None:
            raise RuntimeError("database is not configured")
        return SessionLocal()

    async def ensure_user(self, telegram_id: int) -> None:
        """FK guard: telegram_users row only (never creates a panel account)."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.models import TelegramUser

        async with self._session() as s:
            await s.execute(pg_insert(TelegramUser).values(telegram_id=int(telegram_id))
                            .on_conflict_do_nothing(index_elements=["telegram_id"]))
            await s.commit()

    async def builtin_used(self, code: str, telegram_id: int) -> bool:
        from sqlalchemy import select

        from app.db.models import Payment

        async with self._session() as s:
            r = await s.execute(select(Payment.id).where(Payment.external_id == builtin_external_id(code, telegram_id)))
            return r.first() is not None

    async def trial_used(self, telegram_id: int) -> bool:
        from sqlalchemy import select

        from app.db.models import Trial

        if await self.builtin_used("trial", telegram_id):
            return True
        async with self._session() as s:
            r = await s.execute(select(Trial.id).where(Trial.telegram_user_id == int(telegram_id)))
            return r.first() is not None

    async def record_builtin(self, code: str, telegram_id: int, meta: dict, *,
                             trial: Optional[tuple[str, int]] = None) -> RecordResult:
        from sqlalchemy.exc import IntegrityError

        from app.db.models import Payment, PromoRedemption, Trial

        tg = int(telegram_id)
        now = datetime.utcnow()
        try:
            async with self._session() as s:
                p = Payment(
                    telegram_user_id=tg, provider="promo", external_id=builtin_external_id(code, tg),
                    amount=0, currency="RUB", status="succeeded", description=f"Promo {code}",
                    paid_at=now, payment_metadata=meta, kind="promo",
                    plan_code=(trial[0] if trial else None),
                )
                s.add(p)
                await s.flush()
                r = PromoRedemption(code=code, telegram_user_id=tg, payment_id=p.id, status="pending", reward=None)
                s.add(r)
                if trial:
                    s.add(Trial(telegram_user_id=tg, source="trial", plan_code=trial[0], days=int(trial[1]),
                                payment_id=p.id, started_at=now, ends_at=now + timedelta(days=int(trial[1]))))
                await s.flush()
                rid, pid = r.id, p.id
                await s.commit()
            return RecordResult("ok", payment_id=pid, redemption_id=rid)
        except IntegrityError:
            return RecordResult("used")
        except Exception as e:  # noqa: BLE001
            logger.error(f"promo record {code} tg={tg} failed ({type(e).__name__})")
            return RecordResult("error")

    async def rollback_builtin(self, code: str, telegram_id: int) -> None:
        from sqlalchemy import delete, select

        from app.db.models import Payment, PromoRedemption, Trial

        ext = builtin_external_id(code, telegram_id)
        try:
            async with self._session() as s:
                pid = (await s.execute(select(Payment.id).where(Payment.external_id == ext))).scalar_one_or_none()
                if pid is not None:
                    await s.execute(delete(Trial).where(Trial.payment_id == pid))
                    await s.execute(delete(PromoRedemption).where(PromoRedemption.payment_id == pid))
                    await s.execute(delete(Payment).where(Payment.id == pid, Payment.provider == "promo"))
                await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.error(f"promo rollback {code} tg={telegram_id} failed ({type(e).__name__})")

    async def finish(self, redemption_id: Optional[int], ok: bool, reward: Optional[dict] = None) -> None:
        from sqlalchemy import update

        from app.db.models import PromoCode, PromoRedemption

        if redemption_id is None:
            return
        async with self._session() as s:
            r = await s.get(PromoRedemption, int(redemption_id))
            if r is None:
                return
            r.status = "applied" if ok else "failed"
            if reward is not None:
                r.reward = reward
            if not ok and r.promo_code_id is not None:
                await s.execute(update(PromoCode).where(PromoCode.id == r.promo_code_id, PromoCode.uses > 0)
                                .values(uses=PromoCode.uses - 1))
            await s.commit()

    async def has_paid(self, telegram_id: int) -> bool:
        from sqlalchemy import select

        from app.db.models import Payment

        async with self._session() as s:
            r = await s.execute(select(Payment.id).where(
                Payment.telegram_user_id == int(telegram_id), Payment.status == "succeeded",
                Payment.provider.notin_(NON_REVENUE_PROVIDERS)).limit(1))
            return r.first() is not None

    async def last_paid_plan(self, telegram_id: int) -> Optional[str]:
        from sqlalchemy import select

        from app.db.models import Payment

        async with self._session() as s:
            r = await s.execute(select(Payment).where(
                Payment.telegram_user_id == int(telegram_id), Payment.status == "succeeded",
                Payment.provider.notin_(NON_REVENUE_PROVIDERS)).order_by(Payment.paid_at.desc().nullslast()).limit(1))
            p = r.scalar_one_or_none()
        if p is None:
            return None
        plan = p.plan_code or (p.payment_metadata or {}).get("plan_code")
        return str(plan).lower() if plan else None

    async def get_code(self, code: str) -> Optional[PromoCodeRow]:
        from sqlalchemy import select

        from app.db.models import PromoCode

        async with self._session() as s:
            pc = (await s.execute(select(PromoCode).where(PromoCode.code == normalize_code(code)))).scalar_one_or_none()
            return _row(pc) if pc else None

    async def get_code_by_id(self, code_id: int) -> Optional[PromoCodeRow]:
        from app.db.models import PromoCode

        async with self._session() as s:
            pc = await s.get(PromoCode, int(code_id))
            return _row(pc) if pc else None

    async def reserve(self, code_id: int, telegram_id: int) -> ReserveResult:
        """Atomic: the code row is locked FOR UPDATE, so parallel redemptions
        (other processes, Redis down) are serialized on the database."""
        from sqlalchemy import func, select

        from app.db.models import PromoCode, PromoRedemption

        tg = int(telegram_id)
        async with self._session() as s:
            pc = (await s.execute(select(PromoCode).where(PromoCode.id == int(code_id)).with_for_update())).scalar_one_or_none()
            if pc is None or not pc.is_active:  # switched off meanwhile (refunded gift, admin)
                await s.rollback()
                return ReserveResult("not_found")
            if pc.max_uses is not None and int(pc.uses or 0) >= int(pc.max_uses):
                await s.rollback()
                return ReserveResult("exhausted")
            mine = (await s.execute(select(func.count(PromoRedemption.id)).where(
                PromoRedemption.promo_code_id == pc.id, PromoRedemption.telegram_user_id == tg,
                PromoRedemption.status.in_(("pending", "applied"))))).scalar_one()
            if int(mine) >= int(pc.per_user_limit or 1):
                await s.rollback()
                return ReserveResult("already_used")
            r = PromoRedemption(promo_code_id=pc.id, code=pc.code, telegram_user_id=tg, status="pending")
            s.add(r)
            pc.uses = int(pc.uses or 0) + 1
            await s.flush()
            rid = r.id
            await s.commit()
            return ReserveResult("ok", redemption_id=rid)

    async def create_code(self, spec: PromoCodeSpec, created_by: Optional[int]) -> Optional[PromoCodeRow]:
        from sqlalchemy.exc import IntegrityError

        from app.db.models import PromoCode

        meta = dict(spec.meta or {})
        if spec.traffic_gb:
            meta["traffic_gb"] = int(spec.traffic_gb)
        if spec.devices:
            meta["devices"] = int(spec.devices)
        try:
            async with self._session() as s:
                pc = PromoCode(
                    code=normalize_code(spec.code), kind=spec.kind, plan_code=spec.plan_code, days=spec.days,
                    audience=spec.audience, max_uses=spec.max_uses, uses=0, per_user_limit=spec.per_user_limit,
                    valid_from=_naive(spec.valid_from) if spec.valid_from else None,
                    valid_until=_naive(spec.valid_until) if spec.valid_until else None,
                    is_active=True, created_by=created_by, meta=meta or None,
                )
                s.add(pc)
                await s.commit()
                await s.refresh(pc)
                return _row(pc)
        except IntegrityError:
            return None

    async def list_codes(self, limit: int = 20, *, include_gifts: bool = False) -> list[PromoCodeRow]:
        from sqlalchemy import select

        from app.db.models import PromoCode

        q = select(PromoCode).order_by(PromoCode.id.desc()).limit(int(limit))
        if not include_gifts:
            q = q.where(PromoCode.kind != KIND_GIFT)
        async with self._session() as s:
            return [_row(pc) for pc in (await s.execute(q)).scalars().all()]

    async def set_active(self, code_id: int, active: bool) -> bool:
        from sqlalchemy import update

        from app.db.models import PromoCode

        async with self._session() as s:
            r = await s.execute(update(PromoCode).where(PromoCode.id == int(code_id)).values(is_active=bool(active)))
            await s.commit()
            return bool(r.rowcount)

    async def find_gift_by_payment(self, payment_id: int) -> Optional[str]:
        from sqlalchemy import select

        from app.db.models import PromoCode

        async with self._session() as s:
            r = await s.execute(select(PromoCode.code).where(
                PromoCode.kind == KIND_GIFT, PromoCode.meta["payment_id"].as_string() == str(int(payment_id))).limit(1))
            return r.scalar_one_or_none()
