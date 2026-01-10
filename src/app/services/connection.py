"""Сервис для проверки возможности подключения"""
from datetime import datetime
from typing import Optional
from app.services.users import get_user_active_subscription
from app.services.sync_service import SyncService
from app.logger import logger


async def can_user_connect(telegram_id: int, force_remna: bool = False) -> bool:
    """
    Проверяет, может ли пользователь подключиться к VPN.
    Использует актуальные данные после синхронизации с Remna.
    
    Args:
        telegram_id: Telegram ID пользователя
        force_remna: Принудительная синхронизация с Remna API (игнорирует кэш)
        
    Returns:
        True если подписка активна, False если нет
    """
    try:
        # Синхронизируем с Remna для получения актуальных данных
        sync_service = SyncService()
        tg_name = f"User_{telegram_id}"  # Имя не критично для проверки
        
        try:
            sync_result = await sync_service.sync_user_and_subscription(
                telegram_id=telegram_id,
                tg_name=tg_name,
                use_fallback=True,
                use_cache=not force_remna,  # Не используем кэш, если force_remna=True
                force_sync=force_remna,     # Принудительная синхронизация, если force_remna=True
                force_remna=force_remna     # Принудительно только Remna API, если force_remna=True
            )
            
            # Проверяем статус из синхронизации
            if sync_result and sync_result.subscription_status == "active":
                # Дополнительно проверяем, что подписка не истекла
                if sync_result.expires_at:
                    now = datetime.utcnow()
                    if sync_result.expires_at > now:
                        logger.debug(f"Пользователь {telegram_id} может подключиться (активная подписка)")
                        return True
                    else:
                        logger.debug(f"Пользователь {telegram_id} не может подключиться (подписка истекла)")
                        return False
                else:
                    # Если expires_at нет, но статус active - считаем активной
                    logger.debug(f"Пользователь {telegram_id} может подключиться (подписка активна без даты)")
                    return True
            else:
                logger.debug(f"Пользователь {telegram_id} не может подключиться (статус: {sync_result.subscription_status if sync_result else 'none'})")
                return False
                
        except Exception as e:
            logger.warning(f"Ошибка синхронизации при проверке подключения для {telegram_id}: {e}")
            # Fallback: проверяем данные из БД
            subscription = await get_user_active_subscription(telegram_id, use_cache=False)
            if subscription and subscription.active and subscription.valid_until:
                now = datetime.utcnow()
                if subscription.valid_until > now:
                    logger.debug(f"Пользователь {telegram_id} может подключиться (fallback на БД)")
                    return True
            
            return False
            
    except Exception as e:
        logger.error(f"Ошибка при проверке возможности подключения для {telegram_id}: {e}")
        return False
