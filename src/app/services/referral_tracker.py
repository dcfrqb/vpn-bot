"""
Реферальный трекер /sun718.

Вызывается из yookassa.handle_successful_payment после успешной оплаты.
Если плательщик — приглашённый по /sun718 (есть его promo Payment), шлёт
админу:
  B. Алерт «+N мес от @user, итого M / бонус K.NN»
  C. Алерт «🎁 новый бонусный месяц» при пересечении кратного 5 порога

Бонус считается так же как в /referral_stats (admin.py):
  - только Pro-платежи (plan_code='pro') приглашённых
  - после активации — period_months полностью
  - до активации — последний платёж с покрытием активации даёт +1 мес
  - 5 мес = 1 бонус (дробно)
  - владелец PROMO_SUN718_OWNER_TG_ID исключается
"""
from datetime import timedelta
from typing import Optional

from sqlalchemy import select

from app.config import settings
from app.db.models import Payment, TelegramUser
from app.logger import logger


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


async def compute_sun718_total(session) -> int:
    """Total Pro-месяцев в пуле sun718 (то что показывает /referral_stats)."""
    owner_id = getattr(settings, "PROMO_SUN718_OWNER_TG_ID", None)
    act_res = await session.execute(
        select(Payment.telegram_user_id, Payment.paid_at)
        .where(Payment.provider == "promo")
        .where(Payment.external_id.like("promo_sun718_%"))
    )
    activations = [(tg, dt) for tg, dt in act_res.all() if tg != owner_id]

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


async def notify_referral_payment_if_applicable(bot, session, payment: Payment) -> None:
    """Шлёт алерты B и C если payment — Pro-оплата приглашённого по sun718.

    Soft-fail внутри: исключения логируются, ничего не пробрасывается выше,
    чтобы не сломать основной payment flow.
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
        owner_id = getattr(settings, "PROMO_SUN718_OWNER_TG_ID", None)
        if tg_id == owner_id:
            return  # владелец сам себе не реферал

        # Активировал ли он /sun718?
        promo_res = await session.execute(
            select(Payment)
            .where(Payment.external_id == f"promo_sun718_{tg_id}")
            .where(Payment.provider == "promo")
        )
        promo = promo_res.scalar_one_or_none()
        if not promo:
            return  # не приглашённый

        # Этот платёж был ДО активации? Алерт шлём только для после-активации
        # (до-активационные дают max 1 кредит, отдельный режим, не алертим).
        if payment.paid_at and promo.paid_at and payment.paid_at <= promo.paid_at:
            return

        total_after = await compute_sun718_total(session)
        total_before = max(0, total_after - period_months)
        bonus_after = total_after / 5.0

        # Имя для отображения
        tg_res = await session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == tg_id)
        )
        tg_user = tg_res.scalar_one_or_none()
        username = (f"@{tg_user.username}" if tg_user and tg_user.username
                    else f"ID:<code>{tg_id}</code>")
        name = ""
        if tg_user:
            name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()

        # === Алерт B: +N мес ===
        text_b = (
            f"💰 <b>SUN718: +{period_months} мес к рефералке</b>\n\n"
            f"👤 {username}"
            + (f" ({name})" if name else "")
            + f"\n📦 Оплатил Pro {period_months} мес\n\n"
            f"📊 <b>Текущий пул:</b>\n"
            f"  • Pro-месяцев: <b>{total_after}</b>  (было {total_before})\n"
            f"  • Бонус: <b>{bonus_after:.2f}</b>"
        )
        await _send_admin(bot, text_b)

        # === Алерт C: преодолели целый бонус-порог? ===
        full_before = total_before // 5
        full_after = total_after // 5
        if full_after > full_before:
            delta = full_after - full_before
            word = "месяц" if delta == 1 else ("месяца" if delta < 5 else "месяцев")
            text_c = (
                f"🎁 <b>SUN718: +{delta} бонусн. {word}!</b>\n\n"
                f"Накопилось целое число бонусных месяцев.\n"
                f"Всего к выдаче: <b>{full_after} мес</b>\n"
                f"(дробный остаток: {bonus_after - full_after:.2f})\n\n"
                f"Выдай через стандартные admin grant-кнопки (friend_grant_*)."
            )
            await _send_admin(bot, text_c)

    except Exception as e:
        logger.warning(f"referral_tracker notify soft-fail tg={getattr(payment, 'telegram_user_id', '?')}: {e}")


async def _send_admin(bot, text: str) -> None:
    for admin_id in (settings.ADMINS or []):
        if isinstance(admin_id, int):
            try:
                await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
            except Exception as e:
                logger.error(f"referral_tracker admin notify {admin_id} fail: {e}")
