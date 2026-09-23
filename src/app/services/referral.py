"""Referral code /sun718 (stream E): activation, stats, payouts, revert.

Owner of the code: settings.PROMO_SUN718_OWNER_TG_ID (Zhukov). Rules, as in
2.x (memory project_sun718_referral):

- activation: see ``redeem_sun718`` (called by PromoEngine under its lock);
- stats: only Pro payments of invited users count; after the activation
  all of them (whole period_months), before it the latest Pro payment
  whose period covers the activation gives +1 month; 5 months = 1 bonus;
  the owner is excluded; non-revenue providers (promo, test, ...) never count;
- payouts: a manual ledger (referral_payouts), subtracted from "available";
- revert: an active non-Pro user gets Pro for 5 days on top, then the job
  ``worker/jobs/sun718_revert`` puts the tariff squad back (expiry untouched,
  manual squads and the device limit untouched).

No aiogram here: admin/owner messages go through the Notifier port, the
panel through RemnaGateway.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.domain.models import (
    AdminTopic,
    Entitlement,
    EntitlementSource,
    PromoOutcome,
    PromoReward,
    ensure_utc,
)
from app.domain.texts import fmt_date_msk, h
from app.logger import logger

CODE = "sun718"
MONTHS_PER_BONUS = 5
LIFETIME_YEAR = 2099


def _naive(dt: datetime) -> datetime:
    return ensure_utc(dt).replace(tzinfo=None)


async def redeem_sun718(engine: Any, tg: int, promo: Any) -> PromoReward:
    """Referral code of Zhukov (PROMO_SUN718_OWNER_TG_ID), Pro 5 days.

    Matrix (2.x, memory project_sun718_referral): used before -> refuse;
    lifetime -> refuse, nothing written; no subscription -> Pro 5 days;
    active Pro -> +5 days; active non-Pro -> Pro 5 days on top and a
    revert of the squad after 5 days (worker job sun718_revert). The admin
    gets an alert on every touch. Activations feed /referral_stats.
    """
    code = promo.code
    await engine.repo.ensure_user(tg)
    if await engine.repo.builtin_used(code, tg):
        await engine._alert("⚠️ <b>SUN718: повторная активация</b>", tg, "Повторно не выдавали, в БД ничего не писали.")
        return PromoReward(code=code, outcome=PromoOutcome.ALREADY_USED)
    state = await engine._state(tg)
    if state.stale:
        await engine._alert("❌ <b>SUN718: панель недоступна</b>", tg, "Статус подписки не проверен, ничего не выдали.")
        return PromoReward(code=code, outcome=PromoOutcome.ERROR)
    exp = ensure_utc(state.expires_at)
    if state.is_lifetime or (exp is not None and exp.year >= LIFETIME_YEAR):
        await engine._alert("🌟 <b>SUN718: бессрочная подписка</b>", tg, "Отказ (рефералить бессрочных нельзя), в БД не писали.")
        return PromoReward(code=code, outcome=PromoOutcome.NOT_ELIGIBLE, plan_code="lifetime")
    current_plan: Optional[str] = None
    if state.active:
        current_plan = (state.plan_code or "").lower() or await engine.repo.last_paid_plan(tg)
    schedule_revert = state.active and current_plan != "pro"
    now = datetime.now(timezone.utc)
    meta: dict[str, Any] = {
        "promo_code": code, "tariff": promo.tariff, "auto": True, "provisioned": True,
        "was_active": bool(state.active), "current_plan": current_plan,
    }
    revert_at = now + timedelta(days=promo.days)
    if schedule_revert:
        meta.update({
            "revert_at": _naive(revert_at).isoformat(),
            "pre_promo_plan": current_plan or "basic",
            "pre_promo_expire_at": _naive(exp).isoformat() if exp else None,
            "revert_completed": False,
        })
    rec = await engine.repo.record_builtin(code, tg, meta)
    if rec.status == "used":
        return PromoReward(code=code, outcome=PromoOutcome.ALREADY_USED)
    if rec.status != "ok":
        await engine._alert("❌ <b>SUN718: не записали активацию</b>", tg, "Подписка не выдана (без записи код стал бы многоразовым).")
        return PromoReward(code=code, outcome=PromoOutcome.ERROR)
    ent = Entitlement(plan_code="pro", source=EntitlementSource.PROMO, days=promo.days, note="promo:sun718")
    try:
        new_state = await engine._grant(tg, ent, trace_id=f"promo:sun718:{tg}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"sun718: grant failed tg={tg} ({type(e).__name__})")
        await engine.repo.rollback_builtin(code, tg)
        await engine._alert("❌ <b>SUN718: выдача не удалась</b>", tg, "Запись откатили, пользователь может повторить.")
        return PromoReward(code=code, outcome=PromoOutcome.ERROR)
    await engine.repo.finish(rec.redemption_id, True, {"plan": "pro", "days": promo.days, "revert": schedule_revert})
    if schedule_revert:
        line = (f"📦 Pro {promo.days} дн. поверх {h(current_plan or 'не-Pro')}\n"
                f"🔄 Возврат тарифа: {fmt_date_msk(revert_at, with_time=True)} на {h(current_plan or 'basic')}")
    else:
        line = f"📦 Pro {promo.days} дн.{' (продление)' if state.active else ''}"
    await engine._alert("🎁 <b>SUN718 активирован</b>", tg,
                      f"{line}\n📅 До: {fmt_date_msk(new_state.expires_at)}\n✅ Записано для рефералки")
    return PromoReward(code=code, outcome=PromoOutcome.APPLIED, plan_code="pro", days=promo.days,
                       expires_at=new_state.expires_at, redemption_id=rec.redemption_id)


# --------------------------------------------------------------------------- stats


@dataclass(frozen=True)
class InvitedRow:
    telegram_id: int
    months: int
    payments_after: int
    pre_credit: int


@dataclass(frozen=True)
class ReferralStats:
    code: str
    activations: int
    paying: int
    earned_months: int
    full_bonus: int
    bonus: float
    paid_out: int
    available: int
    owner_id: Optional[int]
    top: list[InvitedRow] = field(default_factory=list)


def _pro_months(p: Any) -> int:
    meta = p.payment_metadata or {}
    plan = str(p.plan_code or meta.get("plan_code") or "").lower()
    if plan != "pro":
        return 0
    raw = p.period_months if p.period_months is not None else meta.get("period_months")
    try:
        m = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        m = 0
    return max(m, 0)


def _owner(settings: Any) -> Optional[int]:
    v = getattr(settings, "PROMO_SUN718_OWNER_TG_ID", None)
    return v if isinstance(v, int) else None


async def compute_stats(session: Any, code: str = CODE, *, settings: Any = None) -> Optional[ReferralStats]:
    """None when the code has no activations."""
    from sqlalchemy import func, select

    from app.db.models import Payment, ReferralPayout
    from app.services.promo import NON_REVENUE_PROVIDERS

    if settings is None:
        from app.config import settings
    code = code.lower()
    owner = _owner(settings) if code == CODE else None
    rows = (await session.execute(
        select(Payment.telegram_user_id, Payment.paid_at)
        .where(Payment.provider == "promo", Payment.external_id.like(f"promo_{code}_%"))
    )).all()
    activations = [(tg, dt) for tg, dt in rows if tg != owner]
    if not activations:
        return None
    per_user: list[InvitedRow] = []
    total = 0
    for tg, activated_at in activations:
        pays = (await session.execute(
            select(Payment).where(
                Payment.telegram_user_id == tg, Payment.status == "succeeded",
                Payment.paid_at.isnot(None), Payment.provider.notin_(NON_REVENUE_PROVIDERS))
        )).scalars().all()
        months = after = 0
        best_pre: Optional[datetime] = None
        for p in pays:
            m = _pro_months(p)
            if m <= 0:
                continue
            if activated_at is not None and p.paid_at > activated_at:
                months += m
                after += 1
            elif activated_at is not None and p.paid_at + timedelta(days=30 * m) >= activated_at:
                if best_pre is None or p.paid_at > best_pre:
                    best_pre = p.paid_at
        pre = 1 if best_pre is not None else 0
        months += pre
        total += months
        per_user.append(InvitedRow(int(tg), months, after, pre))
    paid_out = int((await session.execute(
        select(func.coalesce(func.sum(ReferralPayout.payout_months), 0)).where(ReferralPayout.promo_code == code)
    )).scalar_one() or 0)
    full = total // MONTHS_PER_BONUS
    top = [r for r in sorted(per_user, key=lambda r: -r.months) if r.months > 0][:20]
    return ReferralStats(
        code=code, activations=len(activations), paying=sum(1 for r in per_user if r.months > 0),
        earned_months=total, full_bonus=full, bonus=total / float(MONTHS_PER_BONUS), paid_out=paid_out,
        available=max(0, full - paid_out), owner_id=owner, top=top,
    )


class ReferralService:
    def __init__(self, *, notifier: Any, settings: Any = None, session_factory: Any = None):
        self.notifier = notifier
        self._settings = settings
        self._factory = session_factory

    @property
    def settings(self):
        if self._settings is not None:
            return self._settings
        from app.config import settings

        return settings

    def _session(self):
        if self._factory is not None:
            return self._factory()
        from app.db.session import SessionLocal

        if SessionLocal is None:
            raise RuntimeError("database is not configured")
        return SessionLocal()

    async def stats(self, code: str = CODE) -> Optional[ReferralStats]:
        async with self._session() as s:
            return await compute_stats(s, code, settings=self.settings)

    async def record_payout(self, admin_id: int, months: int, note: str = "") -> tuple[int, int]:
        """Ledger entry for a manual bonus payout. Returns (available before, after)."""
        from app.db.models import ReferralPayout

        async with self._session() as s:
            before = await compute_stats(s, CODE, settings=self.settings)
            s.add(ReferralPayout(admin_id=int(admin_id), promo_code=CODE, payout_months=int(months),
                                 note=(note or "")[:500] or None, created_at=datetime.utcnow()))
            await s.commit()
            after = await compute_stats(s, CODE, settings=self.settings)
        avail_before = before.available if before else 0
        avail_after = after.available if after else 0
        text = (
            f"✅ <b>SUN718: выплата записана</b>\n\n💸 Выплачено: <b>{int(months)} мес</b>\n"
            f"📝 {h(note) or '—'}\n\n"
            f"Заработано: {after.full_bonus if after else 0} целых бонусов "
            f"({after.earned_months if after else 0} Pro-мес)\n"
            f"Выплачено всего: <b>{after.paid_out if after else months}</b>\n"
            f"<b>Доступно к выдаче: {avail_after}</b>"
        )
        await self.notifier.notify_admins(AdminTopic.PROMO, text, html=True)
        owner = _owner(self.settings)
        if owner and owner not in (getattr(self.settings, "ADMINS", None) or []):
            await self.notifier.notify_user(
                owner,
                f"✅ <b>Тебе выдано бонусных месяцев: {int(months)}</b>\n\n"
                + (f"📝 {h(note)}\n" if note else "")
                + f"Осталось доступно: {avail_after} мес. Спасибо за приглашенных!",
                html=True,
            )
        return avail_before, avail_after


# --------------------------------------------------------------------------- revert job


async def due_reverts(session: Any, now: Optional[datetime] = None) -> list[int]:
    """payments.id of sun718 activations whose revert time has come."""
    from sqlalchemy import select

    from app.db.models import Payment

    now = now or datetime.utcnow()
    out: list[int] = []
    rows = (await session.execute(
        select(Payment).where(Payment.provider == "promo", Payment.external_id.like("promo_sun718_%"))
    )).scalars().all()
    for p in rows:
        meta = p.payment_metadata or {}
        if meta.get("revert_completed") or not meta.get("revert_at"):
            continue
        try:
            due = datetime.fromisoformat(str(meta["revert_at"]))
        except ValueError:
            logger.warning(f"sun718_revert: bad revert_at in payment id={p.id}")
            continue
        if due.tzinfo is not None:
            due = due.replace(tzinfo=None) - (due.utcoffset() or timedelta(0))
        if due <= now:
            out.append(p.id)
    return out


def target_squads(current: list[str], target_plan: str) -> list[str]:
    """Non-manual target list: tariff squads replaced by the target plan's
    squad, every other non-manual squad (servers, obhod...) kept. Manual
    squads are kept by the gateway itself and must not be passed."""
    from app.domain.plans import get_plan_squad
    from app.services.remna_tariff import is_manual_squad_name, managed_tariff_squad_names

    managed = managed_tariff_squad_names()
    squad = get_plan_squad(target_plan) or "basic"
    keep = [s for s in current if s not in managed and not is_manual_squad_name(s)]
    return keep + ([squad] if squad not in keep else [])


class Sun718Reverter:
    """One pass of the revert job over ports (RemnaGateway, Notifier, StatusService)."""

    def __init__(self, *, remna: Any, notifier: Any, status: Any = None, session_factory: Any = None):
        self.remna = remna
        self.notifier = notifier
        self.status = status
        self._factory = session_factory

    def _session(self):
        if self._factory is not None:
            return self._factory()
        from app.db.session import SessionLocal

        if SessionLocal is None:
            raise RuntimeError("database is not configured")
        return SessionLocal()

    async def run(self, now: Optional[datetime] = None) -> int:
        async with self._session() as s:
            ids = await due_reverts(s, now)
        done = 0
        for pid in ids:
            try:
                done += int(await self.revert_one(pid))
            except Exception as e:  # noqa: BLE001 - next one still runs
                logger.error(f"sun718_revert: payment id={pid} failed ({type(e).__name__})")
        return done

    async def _panel_id(self, s: Any, tg: int) -> Optional[int]:
        from sqlalchemy import select

        from app.db.models import TelegramUser

        rid = (await s.execute(select(TelegramUser.remna_user_id).where(TelegramUser.telegram_id == tg))).scalar_one_or_none()
        if rid and str(rid).isdigit():
            return int(rid)
        for u in await self.remna.find_users_by_telegram_id(tg):
            if not u.username.endswith("_obhod"):
                return u.id
        return None

    async def revert_one(self, payment_id: int) -> bool:
        from app.db.models import Payment
        from app.services.promo_repo import SqlPromoRepo

        async with self._session() as s:
            p = await s.get(Payment, int(payment_id))
            if p is None or (p.payment_metadata or {}).get("revert_completed"):
                return False
            meta = dict(p.payment_metadata or {})
            tg = int(p.telegram_user_id)
            panel_id = await self._panel_id(s, tg)
        pre = str(meta.get("pre_promo_plan") or "basic").lower()
        paid = await SqlPromoRepo(self._factory).last_paid_plan(tg)
        target = paid if paid == "pro" else pre
        if panel_id is None:
            await self._mark(payment_id, "skipped_no_panel_user")
            await self._alert("⚠️ <b>SUN718 REVERT: пропущен</b>", tg, "Нет аккаунта в панели.")
            return False
        user = await self.remna.get_user(panel_id)
        if user is None:
            await self._mark(payment_id, "skipped_no_panel_user")
            return False
        try:
            await self.remna.update_user(panel_id, squads=target_squads(list(user.squads), target))
        except Exception as e:  # noqa: BLE001 - retry on the next pass
            await self._alert("❌ <b>SUN718 REVERT: сквад не вернули</b>", tg,
                              f"Цель: {h(target)}. Ошибка: {h(type(e).__name__)}. Повторим через час.")
            return False
        if self.status is not None:
            try:
                await self.status.invalidate(tg)
            except Exception:  # noqa: BLE001
                pass
        await self._mark(payment_id, target)
        extra = "\nПользователь докупил Pro, Pro остался." if (paid == "pro" and pre != "pro") else ""
        await self._alert("🔄 <b>SUN718 REVERT выполнен</b>", tg, f"Тариф: {h(pre)} → <b>{h(target)}</b>{extra}")
        return True

    async def _mark(self, payment_id: int, reverted_to: str) -> None:
        from app.db.models import Payment

        async with self._session() as s:
            p = await s.get(Payment, int(payment_id))
            if p is None:
                return
            meta = dict(p.payment_metadata or {})
            meta.update({"revert_completed": True, "reverted_at": datetime.utcnow().isoformat(),
                         "reverted_to_plan": reverted_to})
            p.payment_metadata = meta
            await s.commit()

    async def _alert(self, title: str, tg: int, body: str) -> None:
        try:
            await self.notifier.notify_admins(AdminTopic.PROMO, f"{title}\n\n🆔 <code>{tg}</code>\n{body}", html=True)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["CODE", "ReferralStats", "InvitedRow", "compute_stats", "ReferralService", "due_reverts",
           "target_squads", "Sun718Reverter", "redeem_sun718"]
