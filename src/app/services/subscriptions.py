"""Сервис для работы с подписками и уведомлениями"""
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Subscription, TelegramUser
from app.db.session import SessionLocal
from app.logger import logger


async def check_expired_subscriptions() -> Dict[str, int]:
    """Проверяет и деактивирует истекшие подписки"""
    if not SessionLocal:
        return {"expired": 0, "errors": 0}
    
    now = datetime.utcnow()
    expired_count = 0
    error_count = 0
    
    async with SessionLocal() as session:
        try:
            result = await session.execute(
                select(Subscription)
                .where(
                    Subscription.active == True,
                    Subscription.valid_until.isnot(None),
                    Subscription.valid_until <= now
                )
            )
            expired_subs = result.scalars().all()
            
            for sub in expired_subs:
                try:
                    sub.active = False
                    expired_count += 1
                    logger.info(f"Подписка {sub.id} истекла для пользователя {sub.telegram_user_id}")
                except Exception as e:
                    logger.error(f"Ошибка при деактивации подписки {sub.id}: {e}")
                    error_count += 1
            
            if expired_count > 0:
                await session.commit()
                logger.info(f"Деактивировано {expired_count} истекших подписок")
            
        except Exception as e:
            logger.error(f"Ошибка при проверке истекших подписок: {e}")
            error_count += 1
    
    return {"expired": expired_count, "errors": error_count}


async def get_subscriptions_expiring_soon(days: int) -> List[Dict[str, Any]]:
    """Получает подписки, которые истекают через указанное количество дней"""
    if not SessionLocal:
        return []
    
    now = datetime.utcnow()
    target_date = now + timedelta(days=days)
    if days > 0:
        start_date = now + timedelta(days=days - 1)
    else:
        start_date = now
        target_date = now + timedelta(days=1)
    
    subscriptions = []
    
    async with SessionLocal() as session:
        try:
            result = await session.execute(
                select(Subscription, TelegramUser)
                .join(TelegramUser, Subscription.telegram_user_id == TelegramUser.telegram_id)
                .where(
                    Subscription.active == True,
                    Subscription.valid_until.isnot(None),
                    Subscription.valid_until >= start_date,
                    Subscription.valid_until <= target_date
                )
            )
            
            for sub, user in result.all():
                days_left = (sub.valid_until - now).days
                subscriptions.append({
                    "subscription": sub,
                    "user": user,
                    "days_left": days_left,
                    "expires_at": sub.valid_until
                })
            
        except Exception as e:
            logger.error(f"Ошибка при получении подписок, истекающих через {days} дней: {e}")
    
    return subscriptions


async def send_expiration_notification(
    bot,
    telegram_user_id: int,
    days_left: int,
    subscription: Subscription
) -> bool:
    """Отправляет уведомление пользователю об истечении подписки"""
    try:
        plan_name = subscription.plan_name or subscription.plan_code.upper()
        expires_at_str = subscription.valid_until.strftime("%d.%m.%Y %H:%M")
        
        if days_left == 0:
            message = (
                f"⚠️ <b>Ваша подписка истекает сегодня!</b>\n\n"
                f"💳 Тариф: {plan_name}\n"
                f"📅 Истекает: {expires_at_str}\n\n"
                f"Продлите подписку, чтобы продолжить пользоваться VPN."
            )
        elif days_left == 1:
            message = (
                f"⏰ <b>Ваша подписка истекает завтра!</b>\n\n"
                f"💳 Тариф: {plan_name}\n"
                f"📅 Истекает: {expires_at_str}\n"
                f"⏰ Осталось: 1 день\n\n"
                f"Продлите подписку, чтобы не потерять доступ к VPN."
            )
        elif days_left == 3:
            message = (
                f"📅 <b>Ваша подписка истекает через 3 дня</b>\n\n"
                f"💳 Тариф: {plan_name}\n"
                f"📅 Истекает: {expires_at_str}\n"
                f"⏰ Осталось: 3 дня\n\n"
                f"Не забудьте продлить подписку!"
            )
        else:
            message = (
                f"📅 <b>Напоминание о подписке</b>\n\n"
                f"💳 Тариф: {plan_name}\n"
                f"📅 Истекает: {expires_at_str}\n"
                f"⏰ Осталось: {days_left} дней\n\n"
                f"Продлите подписку, чтобы продолжить пользоваться VPN."
            )
        
        await bot.send_message(
            chat_id=telegram_user_id,
            text=message,
            parse_mode="HTML"
        )
        
        logger.info(f"Отправлено уведомление пользователю {telegram_user_id} (осталось {days_left} дней)")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю {telegram_user_id}: {e}")
        return False


async def process_subscription_notifications(bot) -> Dict[str, int]:
    """Обрабатывает уведомления об истечении подписок"""
    if not SessionLocal:
        return {"notified_3d": 0, "notified_1d": 0, "notified_0d": 0, "errors": 0}
    
    notified_3d = 0
    notified_1d = 0
    notified_0d = 0
    errors = 0
    
    try:
        subs_3d = await get_subscriptions_expiring_soon(3)
        for sub_data in subs_3d:
            if sub_data["days_left"] == 3:
                if await send_expiration_notification(
                    bot, sub_data["user"].telegram_id, 3, sub_data["subscription"]
                ):
                    notified_3d += 1
                else:
                    errors += 1
        
        subs_1d = await get_subscriptions_expiring_soon(1)
        for sub_data in subs_1d:
            if sub_data["days_left"] == 1:
                if await send_expiration_notification(
                    bot, sub_data["user"].telegram_id, 1, sub_data["subscription"]
                ):
                    notified_1d += 1
                else:
                    errors += 1
        
        subs_0d = await get_subscriptions_expiring_soon(0)
        for sub_data in subs_0d:
            if sub_data["days_left"] == 0:
                if await send_expiration_notification(
                    bot, sub_data["user"].telegram_id, 0, sub_data["subscription"]
                ):
                    notified_0d += 1
                else:
                    errors += 1
        
    except Exception as e:
        logger.error(f"Ошибка при обработке уведомлений: {e}")
        errors += 1
    
    return {
        "notified_3d": notified_3d,
        "notified_1d": notified_1d,
        "notified_0d": notified_0d,
        "errors": errors
    }


async def create_trial_subscription(telegram_user_id: int) -> Optional[Subscription]:
    """
    Создает пробную подписку на 2 дня для нового пользователя.
    Возвращает созданную подписку или None, если подписка не была создана.
    """
    if not SessionLocal:
        logger.warning("БД не настроена, пробная подписка не может быть создана")
        return None
    
    async with SessionLocal() as session:
        try:
            # Проверяем, есть ли у пользователя уже какие-либо подписки
            result = await session.execute(
                select(Subscription).where(
                    Subscription.telegram_user_id == telegram_user_id
                )
            )
            existing_subs = result.scalars().all()
            
            # Если у пользователя уже были подписки, не создаем пробную
            if existing_subs:
                logger.info(f"У пользователя {telegram_user_id} уже были подписки, пробная подписка не создается")
                return None
            
            # Проверяем, что пользователь существует
            from app.db.models import TelegramUser
            user_result = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
            )
            user = user_result.scalar_one_or_none()
            
            if not user:
                logger.warning(f"Пользователь {telegram_user_id} не найден, пробная подписка не создается")
                return None
            
            # Создаем пробную подписку на 2 дня
            valid_until = datetime.utcnow() + timedelta(days=2)
            trial_subscription = Subscription(
                telegram_user_id=telegram_user_id,
                plan_code="trial",
                plan_name="Пробный период",
                active=True,
                valid_until=valid_until
            )
            session.add(trial_subscription)
            await session.commit()
            await session.refresh(trial_subscription)
            
            logger.info(f"Создана пробная подписка на 2 дня для пользователя {telegram_user_id}")
            
            # Создаем пользователя в Remna API для пробной подписки в фоне (не блокируем ответ)
            import asyncio
            async def create_remna_user_background():
                try:
                    from app.services.payments.yookassa import get_or_create_remna_user_and_get_subscription_url
                    subscription_url = await get_or_create_remna_user_and_get_subscription_url(
                        telegram_user_id=telegram_user_id,
                        subscription_id=trial_subscription.id
                    )
                    if subscription_url:
                        logger.info(f"Пользователь создан в Remna API для пробной подписки {telegram_user_id}")
                except Exception as remna_error:
                    # Не критично, если не удалось создать пользователя в Remna сразу
                    logger.warning(f"Не удалось создать пользователя в Remna API для пробной подписки: {remna_error}")
            
            # Запускаем в фоне, не ждем результата
            asyncio.create_task(create_remna_user_background())
            
            return trial_subscription
            
        except Exception as e:
            logger.error(f"Ошибка при создании пробной подписки для пользователя {telegram_user_id}: {e}")
            await session.rollback()
            return None

