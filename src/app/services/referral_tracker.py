"""
Реферальный трекер /sun718.

Учёт:
  - earned  — Pro-месяцев заработано пулом приглашённых (lifetime, как раньше)
  - paid_out — сумма ручных выплат через /referral_payout (lifetime)
  - available = earned // 5  −  paid_out_bonus_months

Алерты:
  - АДМИНУ (settings.ADMINS) — детальные на каждый Pro-платёж приглашённого:
    B: «+N мес, пул X, бонус Y, доступно Z»
    C: «+M бонусн. месяцев заработано» при пересечении порога / 5
  - ВЛАДЕЛЬЦУ (settings.PROMO_SUN718_OWNER_TG_ID) — упрощённые те же события:
    B: «спасибо, новая оплата приглашённого — пул X, доступно Y»
    C: «🎁 заработан N бонусн. месяц(ев) — доступно X»
    Payout: «✅ вам выдано N мес, осталось доступно X»
  Если admin_id == owner_id — шлём ТОЛЬКО админскую (она информативнее).

Вызывается из:
  - yookassa.handle_successful_payment → notify_referral_payment_if_applicable
  - admin.cmd_referral_payout → record_payout + notify_payout
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.db.models import Payment, TelegramUser
from app.logger import logger


# === Helpers ===

def _pro_months(p: Payment) -> int:
    """period_months если plan_code=pro и положительный, иначе 0."""
    meta = p.payment_metadata or {}
    if str(meta.get("plan_code") or "").lower() != "pro":
        return 0
    pm = meta.get("period_months")
    try:
        m = int(pm) if pm is not None else 0
    except (TypeError, ValueError):
        m = 0
    return m if m > 0 else 0


def _owner_id() -> Optional[int]:
    v = getattr(settings, "PROMO_SUN718_OWNER_TG_ID", None)
    return v if isinstance(v, int) else None


def _admin_ids() -> list[int]:
    return [a for a in (settings.ADMINS or []) if isinstance(a, int)]


# === Core computation ===

async def compute_sun718_earned_months(session) -> int:
    """Сумма Pro-месяцев в пуле sun718 (earned, lifetime)."""
    owner = _owner_id()
    act_res = await session.execute(
        select(Payment.telegram_user_id, Payment.paid_at)
        .where(Payment.provider == "promo")
        .where(Payment.external_id.like("promo_sun718_%"))
    )
    activations = [(tg, dt) for tg, dt in act_res.all() if tg != owner]

    total = 0
    for tg_id, activated_at in activations:
        pays_res = await session.execute(
            select(Payment)
            .where(Payment.telegram_user_id == tg_id)
            .where(Payment.provider != "promo")
            .where(Payment.status == "succeeded")
            .where(Payment.paid_at != None)  # noqa: E711
        )
        user_months = 0
        best_pre = None
        for p in pays_res.scalars():
            m = _pro_months(p)
            if m <= 0:
                continue
            if p.paid_at > activated_at:
                user_months += m
            else:
                coverage_end = p.paid_at + timedelta(days=30 * m)
                if coverage_end >= activated_at:
                    if best_pre is None or p.paid_at > best_pre[0]:
                        best_pre = (p.paid_at, m)
        if best_pre is not None:
            user_months += 1
        total += user_months
    return total


async def compute_sun718_paid_out(session) -> int:
    """Сумма выплаченных бонусных месяцев (из журнала /referral_payout)."""
    res = await session.execute(
        select(Payment)
        .where(Payment.provider == "referral_payout")
        .where(Payment.external_id.like("referral_payout_sun718_%"))
        .where(Payment.status == "succeeded")
    )
    total = 0
    for p in res.scalars():
        meta = p.payment_metadata or {}
        try:
            n = int(meta.get("payout_months") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            total += n
    return total


async def compute_sun718_breakdown(session) -> dict:
    """Возвращает {earned, full_bonus, paid_out, available} для /referral_stats."""
    earned = await compute_sun718_earned_months(session)
    paid_out = await compute_sun718_paid_out(session)
    full_bonus = earned // 5
    available = max(0, full_bonus - paid_out)
    return {
        "earned_months": earned,
        "full_bonus_months": full_bonus,  # целые заработанные бонусы
        "fractional_bonus": (earned / 5.0) - full_bonus,
        "paid_out_months": paid_out,
        "available_months": available,
    }


# === Notify: payment of invited user ===

async def notify_referral_payment_if_applicable(bot, session, payment: Payment) -> None:
    """Алерты B и C при Pro-платеже приглашённого. Soft-fail внутри.

    Шлёт админу полную версию + владельцу упрощённую (если задан owner_id и
    он отличается от admin'а).
    """
    try:
        if not payment:
            return
        if payment.status != "succeeded" or payment.provider == "promo":
            return
        period_months = _pro_months(payment)
        if period_months <= 0:
            return
        tg_id = payment.telegram_user_id
        owner_id = _owner_id()
        if tg_id == owner_id:
            return

        promo_res = await session.execute(
            select(Payment)
            .where(Payment.external_id == f"promo_sun718_{tg_id}")
            .where(Payment.provider == "promo")
        )
        promo = promo_res.scalar_one_or_none()
        if not promo:
            return
        if payment.paid_at and promo.paid_at and payment.paid_at <= promo.paid_at:
            return

        # Считаем earned до и после, плюс paid_out (одинаково)
        earned_after = await compute_sun718_earned_months(session)
        earned_before = max(0, earned_after - period_months)
        paid_out = await compute_sun718_paid_out(session)
        available_after = max(0, earned_after // 5 - paid_out)
        available_before = max(0, earned_before // 5 - paid_out)
        bonus_after = earned_after / 5.0

        # Юзер-данные
        tg_res = await session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == tg_id)
        )
        tg_user = tg_res.scalar_one_or_none()
        username = (f"@{tg_user.username}" if tg_user and tg_user.username
                    else f"ID:<code>{tg_id}</code>")
        name = ""
        if tg_user:
            name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()

        # === ADMIN B ===
        admin_b = (
            f"💰 <b>SUN718: +{period_months} мес к рефералке</b>\n\n"
            f"👤 {username}" + (f" ({name})" if name else "") +
            f"\n📦 Оплатил Pro {period_months} мес\n\n"
            f"📊 <b>Текущий пул:</b>\n"
            f"  • Pro-месяцев: <b>{earned_after}</b>  (было {earned_before})\n"
            f"  • Бонус заработано: <b>{bonus_after:.2f}</b>  (целых: {earned_after // 5})\n"
            f"  • Уже выплачено: <b>{paid_out}</b>\n"
            f"  • <b>Доступно к выдаче: {available_after}</b>"
        )
        await _send_to(bot, _admin_ids(), admin_b)

        # === OWNER B (если задан и не совпадает с админом) ===
        if owner_id and owner_id not in _admin_ids():
            owner_b = (
                f"💰 <b>Новая оплата приглашённого!</b>\n\n"
                f"Один из приглашённых вами юзеров оплатил Pro на "
                f"<b>{period_months} мес</b>.\n\n"
                f"📊 <b>Ваш прогресс:</b>\n"
                f"  • Заработано Pro-месяцев: <b>{earned_after}</b>\n"
                f"  • Бонусных месяцев: <b>{bonus_after:.2f}</b>\n"
                f"  • <b>Доступно к выдаче: {available_after} мес</b>"
            )
            await _send_to(bot, [owner_id], owner_b)

        # === ALERT C: преодолели целый порог / 5 ===
        full_before = earned_before // 5
        full_after = earned_after // 5
        if full_after > full_before:
            delta = full_after - full_before
            word = "месяц" if delta == 1 else ("месяца" if delta < 5 else "месяцев")
            admin_c = (
                f"🎁 <b>SUN718: +{delta} бонусн. {word} заработано!</b>\n\n"
                f"Целых бонусов всего: <b>{full_after}</b>\n"
                f"Уже выплачено: <b>{paid_out}</b>\n"
                f"<b>Доступно к выдаче: {available_after} мес</b>\n\n"
                f"Выдай в Remna вручную и зафиксируй: "
                f"<code>/referral_payout sun718 N</code>"
            )
            await _send_to(bot, _admin_ids(), admin_c)
            if owner_id and owner_id not in _admin_ids():
                owner_c = (
                    f"🎁 <b>Поздравляем! +{delta} бонусн. {word}!</b>\n\n"
                    f"Вы заработали ещё один целый бонусный месяц подписки.\n\n"
                    f"📊 <b>Всего заработано бонусов:</b> {full_after} мес\n"
                    f"✅ <b>Доступно к выдаче:</b> {available_after} мес\n\n"
                    f"Свяжитесь с админом для выдачи."
                )
                await _send_to(bot, [owner_id], owner_c)

    except Exception as e:
        logger.warning(
            f"referral_tracker notify soft-fail "
            f"tg={getattr(payment, 'telegram_user_id', '?')}: {e}"
        )


# === Payout: запись выплаты + алерт ===

async def record_payout(
    session, *, months: int, note: str, admin_id: int
) -> Payment:
    """Записывает выплату бонуса в Payment как provider='referral_payout'.

    Возвращает созданный Payment. Идемпотентность не гарантируется — каждый
    вызов = новая запись (админ должен сам не дублировать).
    """
    now = datetime.utcnow()
    ext_id = f"referral_payout_sun718_{int(now.timestamp())}_{admin_id}"
    rec = Payment(
        telegram_user_id=admin_id,  # FK на админа который выдал
        provider="referral_payout",
        external_id=ext_id,
        amount=0,
        currency="RUB",
        status="succeeded",
        description=f"Sun718 payout: {months} мес ({note or 'без комментария'})",
        paid_at=now,
        payment_metadata={
            "promo_code": "sun718",
            "payout_months": int(months),
            "note": str(note or "")[:500],
            "admin_id": admin_id,
        },
    )
    session.add(rec)
    await session.commit()
    return rec


async def notify_payout(bot, session, payout: Payment) -> None:
    """Алерт о ручной выплате — админу + владельцу (если задан)."""
    meta = payout.payment_metadata or {}
    months = int(meta.get("payout_months") or 0)
    note = meta.get("note") or ""

    paid_out = await compute_sun718_paid_out(session)
    earned = await compute_sun718_earned_months(session)
    available = max(0, earned // 5 - paid_out)

    admin_text = (
        f"✅ <b>SUN718 PAYOUT записана</b>\n\n"
        f"💸 Выплачено: <b>{months} мес</b>\n"
        f"📝 Note: {note or '<i>—</i>'}\n\n"
        f"📊 <b>Состояние:</b>\n"
        f"  • Заработано: {earned // 5} целых бонусов ({earned} Pro-мес)\n"
        f"  • Выплачено всего: <b>{paid_out}</b>\n"
        f"  • <b>Доступно к выдаче: {available}</b>"
    )
    await _send_to(bot, _admin_ids(), admin_text)

    owner_id = _owner_id()
    if owner_id and owner_id not in _admin_ids():
        word = "месяц" if months == 1 else ("месяца" if months < 5 else "месяцев")
        owner_text = (
            f"✅ <b>Вам выдано {months} бонусн. {word}!</b>\n\n"
            f"Админ продлил вашу подписку.\n"
            + (f"📝 Комментарий: {note}\n\n" if note else "\n")
            + f"📊 <b>Осталось доступно:</b> {available} мес\n"
            f"Спасибо за приглашённых!"
        )
        await _send_to(bot, [owner_id], owner_text)


# === Sender ===

async def _send_to(bot, chat_ids: list[int], text: str) -> None:
    for cid in chat_ids:
        if not isinstance(cid, int):
            continue
        try:
            await bot.send_message(chat_id=cid, text=text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"referral_tracker send to {cid} fail: {e}")
