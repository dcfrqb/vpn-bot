"""Стоп-лист: кому не продаём.

Две таблицы:
  blocked_users  — telegram_id, проверяется ДО создания платежа (деньги не берём);
  blocked_cards  — отпечаток карты (first6-last4-MM/YY), проверяется в вебхуке
                   уже после оплаты, поэтому только уведомляет админа.

Заполняется вручную через SQL, без UI:
  INSERT INTO blocked_users (telegram_id, reason) VALUES (123456, 'конкурент');
  INSERT INTO blocked_cards (fingerprint, reason) VALUES ('220220-7882-09/2028', 'конкурент');
"""
from typing import Optional, Dict, Any

from sqlalchemy import text

from app.db.session import SessionLocal
from app.logger import logger


def card_fingerprint(card: Optional[Dict[str, Any]]) -> Optional[str]:
    """Отпечаток карты из объекта payment_method.card в вебхуке YooKassa."""
    if not card:
        return None
    first6 = card.get("first6")
    last4 = card.get("last4")
    month = card.get("expiry_month")
    year = card.get("expiry_year")
    if not (first6 and last4 and month and year):
        return None
    return f"{first6}-{last4}-{month}/{year}"


async def get_user_block_reason(telegram_id: int) -> Optional[str]:
    """Причина блокировки пользователя, или None. Ошибки БД не блокируют продажу."""
    if not SessionLocal or not telegram_id:
        return None
    try:
        async with SessionLocal() as session:
            row = await session.execute(
                text("SELECT reason FROM blocked_users WHERE telegram_id = :tid"),
                {"tid": int(telegram_id)},
            )
            found = row.scalar_one_or_none()
            return found if found is not None else None
    except Exception as e:
        logger.warning(f"blocklist: user check failed tg_id={telegram_id} err={e}")
        return None


async def get_card_block_reason(fingerprint: Optional[str]) -> Optional[str]:
    """Причина блокировки карты, или None."""
    if not SessionLocal or not fingerprint:
        return None
    try:
        async with SessionLocal() as session:
            row = await session.execute(
                text("SELECT reason FROM blocked_cards WHERE fingerprint = :fp"),
                {"fp": fingerprint},
            )
            found = row.scalar_one_or_none()
            return found if found is not None else None
    except Exception as e:
        logger.warning(f"blocklist: card check failed fp={fingerprint} err={e}")
        return None


async def notify_admins(text_message: str) -> None:
    """Шлёт сообщение админам из settings.ADMINS. Молча проглатывает ошибки."""
    try:
        from aiogram import Bot
        from app.config import settings

        admins = getattr(settings, "ADMINS", None) or []
        if isinstance(admins, (str, int)):
            admins = [admins]
        token = getattr(settings, "BOT_TOKEN", None)
        if not token or not admins:
            return
        bot = Bot(token=str(token))
        try:
            for admin_id in admins:
                try:
                    await bot.send_message(int(admin_id), text_message, parse_mode="HTML")
                except Exception as e:
                    logger.warning(f"blocklist: notify admin {admin_id} failed: {e}")
        finally:
            await bot.session.close()
    except Exception as e:
        logger.warning(f"blocklist: notify_admins failed: {e}")
