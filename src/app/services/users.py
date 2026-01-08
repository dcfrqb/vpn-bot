"""Сервис для работы с пользователями"""
from datetime import datetime
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TelegramUser, Subscription
from app.db.session import SessionLocal
from app.logger import logger


async def get_or_create_telegram_user(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    language_code: Optional[str] = None,
    create_trial: bool = True
) -> TelegramUser:
    """
    Получает или создает пользователя Telegram в БД.
    
    Args:
        telegram_id: ID пользователя в Telegram
        username: Username пользователя
        first_name: Имя пользователя
        last_name: Фамилия пользователя
        language_code: Код языка пользователя
        create_trial: Создавать ли пробную подписку для нового пользователя (по умолчанию True)
    """
    if not SessionLocal:
        logger.warning("БД не настроена, пользователь не будет сохранен")
        class MockUser:
            telegram_id = telegram_id
            username = username
            first_name = first_name
            last_name = last_name
            language_code = language_code
            is_admin = False
            created_at = datetime.utcnow()
            last_activity_at = datetime.utcnow()
        return MockUser()
    
    async with SessionLocal() as session:
        result = await session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        is_new_user = False
        
        if user:
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.language_code = language_code
            user.last_activity_at = datetime.utcnow()
            logger.debug(f"Обновлен пользователь {telegram_id}")
        else:
            user = TelegramUser(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
                last_activity_at=datetime.utcnow()
            )
            session.add(user)
            is_new_user = True
            logger.info(f"Создан новый пользователь {telegram_id} (@{username})")
        
        await session.commit()
        await session.refresh(user)
        
        # Создаем пробную подписку для нового пользователя
        if is_new_user and create_trial:
            from app.services.subscriptions import create_trial_subscription
            trial_sub = await create_trial_subscription(telegram_id)
            if trial_sub:
                logger.info(f"Пробная подписка создана для нового пользователя {telegram_id}")
        
        return user


async def update_user_activity(telegram_id: int) -> None:
    """Обновляет время последней активности пользователя"""
    if not SessionLocal:
        return
    
    async with SessionLocal() as session:
        result = await session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )
        user = result.scalar_one_or_none()
        
        if user:
            user.last_activity_at = datetime.utcnow()
            await session.commit()


async def get_user_active_subscription(telegram_id: int, use_cache: bool = True) -> Optional[Subscription]:
    """Получает активную подписку пользователя с кэшированием"""
    # Проверяем кэш
    if use_cache:
        try:
            from app.services.cache import get_cached_subscription, set_cached_subscription
            cached = await get_cached_subscription(telegram_id)
            if cached is not None:
                # Если кэш содержит пустой dict (нет подписки), возвращаем None
                if cached == {}:
                    return None
                # Восстанавливаем объект Subscription из кэша
                if isinstance(cached, dict) and 'id' in cached:
                    # Создаем объект из кэшированных данных
                    sub_data = cached.copy()
                    if sub_data.get('valid_until'):
                        sub_data['valid_until'] = datetime.fromisoformat(sub_data['valid_until'])
                    # Создаем минимальный объект Subscription (используем глобальный импорт)
                    subscription = Subscription()
                    for key, value in sub_data.items():
                        setattr(subscription, key, value)
                    logger.debug(f"Подписка получена из кэша для пользователя {telegram_id}")
                    return subscription
        except Exception as e:
            logger.debug(f"Ошибка получения из кэша: {e}")
    
    if not SessionLocal:
        return None
    
    # Получаем из БД
    async with SessionLocal() as session:
        now = datetime.utcnow()
        result = await session.execute(
            select(Subscription)
            .join(TelegramUser, Subscription.telegram_user_id == TelegramUser.telegram_id)
            .where(
                TelegramUser.telegram_id == telegram_id,
                Subscription.active == True,
                (Subscription.valid_until.is_(None)) | (Subscription.valid_until > now)
            )
            .order_by(Subscription.created_at.desc())
        )
        subscription = result.scalar_one_or_none()
        
        # Сохраняем в кэш
        if use_cache:
            try:
                from app.services.cache import set_cached_subscription
                if subscription:
                    # Сохраняем только необходимые поля
                    cache_data = {
                        'id': subscription.id,
                        'telegram_user_id': subscription.telegram_user_id,
                        'remna_user_id': subscription.remna_user_id,
                        'plan_code': subscription.plan_code,
                        'plan_name': subscription.plan_name,
                        'active': subscription.active,
                        'valid_until': subscription.valid_until.isoformat() if subscription.valid_until else None,
                        'config_data': subscription.config_data,
                        'remna_subscription_id': subscription.remna_subscription_id,
                    }
                    await set_cached_subscription(telegram_id, cache_data)
                else:
                    # Кэшируем отсутствие подписки
                    await set_cached_subscription(telegram_id, {})
            except Exception as e:
                logger.debug(f"Ошибка сохранения в кэш: {e}")
        
        return subscription

