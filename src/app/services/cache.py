"""Сервис кэширования данных пользователей и подписок"""
import json
import pickle
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from app.config import settings
from app.logger import logger

_redis_client = None
_cache_enabled = False

def get_redis_client():
    """Получает клиент Redis"""
    global _redis_client, _cache_enabled
    
    if _redis_client is not None:
        return _redis_client
    
    if not settings.REDIS_URL:
        logger.debug("Redis не настроен, кэширование отключено")
        _cache_enabled = False
        return None
    
    try:
        import redis.asyncio as redis
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=False)
        _cache_enabled = True
        logger.info("Redis клиент для кэширования инициализирован")
        return _redis_client
    except Exception as e:
        logger.warning(f"Не удалось подключиться к Redis для кэширования: {e}")
        _cache_enabled = False
        return None


# Ключи кэша
CACHE_TTL = 300  # Время жизни кэша в секундах (5 минут) - увеличено для лучшей производительности
USER_CACHE_PREFIX = "user:"
SUBSCRIPTION_CACHE_PREFIX = "sub:"
USER_SUB_CACHE_PREFIX = "user_sub:"
PLANS_CACHE_PREFIX = "plans:"
CONFIG_CACHE_PREFIX = "config:"


async def get_cached_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получает пользователя из кэша"""
    client = get_redis_client()
    if not client:
        return None
    
    try:
        key = f"{USER_CACHE_PREFIX}{telegram_id}"
        data = await client.get(key)
        if data:
            return pickle.loads(data)
    except Exception as e:
        logger.debug(f"Ошибка получения пользователя из кэша: {e}")
    
    return None


async def set_cached_user(telegram_id: int, user_data: Dict[str, Any], ttl: int = CACHE_TTL):
    """Сохраняет пользователя в кэш"""
    client = get_redis_client()
    if not client:
        return
    
    try:
        key = f"{USER_CACHE_PREFIX}{telegram_id}"
        data = pickle.dumps(user_data)
        await client.setex(key, ttl, data)
    except Exception as e:
        logger.debug(f"Ошибка сохранения пользователя в кэш: {e}")


async def get_cached_subscription(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Получает подписку из кэша"""
    client = get_redis_client()
    if not client:
        return None
    
    try:
        key = f"{SUBSCRIPTION_CACHE_PREFIX}{telegram_id}"
        data = await client.get(key)
        if data:
            return pickle.loads(data)
    except Exception as e:
        logger.debug(f"Ошибка получения подписки из кэша: {e}")
    
    return None


async def set_cached_subscription(telegram_id: int, subscription_data: Optional[Dict[str, Any]], ttl: int = CACHE_TTL):
    """Сохраняет подписку в кэш"""
    client = get_redis_client()
    if not client:
        return
    
    try:
        key = f"{SUBSCRIPTION_CACHE_PREFIX}{telegram_id}"
        if subscription_data is None:
            # Кэшируем None на короткое время (10 секунд), чтобы не делать лишние запросы
            await client.setex(key, 10, pickle.dumps(None))
        else:
            data = pickle.dumps(subscription_data)
            await client.setex(key, ttl, data)
    except Exception as e:
        logger.debug(f"Ошибка сохранения подписки в кэш: {e}")


async def invalidate_user_cache(telegram_id: int):
    """Инвалидирует кэш пользователя"""
    client = get_redis_client()
    if not client:
        return
    
    try:
        await client.delete(f"{USER_CACHE_PREFIX}{telegram_id}")
        await client.delete(f"{SUBSCRIPTION_CACHE_PREFIX}{telegram_id}")
        await client.delete(f"{USER_SUB_CACHE_PREFIX}{telegram_id}")
    except Exception as e:
        logger.debug(f"Ошибка инвалидации кэша: {e}")


async def invalidate_subscription_cache(telegram_id: int):
    """Инвалидирует кэш подписки"""
    client = get_redis_client()
    if not client:
        return
    
    try:
        await client.delete(f"{SUBSCRIPTION_CACHE_PREFIX}{telegram_id}")
        await client.delete(f"{USER_SUB_CACHE_PREFIX}{telegram_id}")
    except Exception as e:
        logger.debug(f"Ошибка инвалидации кэша подписки: {e}")


async def cleanup_expired_cache():
    """Автоматическая очистка истекших ключей кэша"""
    client = get_redis_client()
    if not client:
        return
    
    try:
        # Redis автоматически удаляет ключи с истекшим TTL, но можно принудительно очистить
        # Используем SCAN для поиска всех ключей кэша
        cursor = 0
        deleted_count = 0
        
        while True:
            cursor, keys = await client.scan(cursor, match=f"{SUBSCRIPTION_CACHE_PREFIX}*", count=100)
            if keys:
                # Проверяем TTL каждого ключа
                for key in keys:
                    ttl = await client.ttl(key)
                    if ttl == -1:  # Ключ без TTL (не должен быть, но на всякий случай)
                        await client.delete(key)
                        deleted_count += 1
                    elif ttl == -2:  # Ключ уже истек (не должен быть в результатах, но проверим)
                        deleted_count += 1
            
            if cursor == 0:
                break
        
        if deleted_count > 0:
            logger.debug(f"Очищено {deleted_count} истекших ключей кэша")
    except Exception as e:
        logger.debug(f"Ошибка при очистке кэша: {e}")


async def get_cached_plans() -> Optional[Dict[str, Any]]:
    """Получает тарифы из кэша"""
    client = get_redis_client()
    if not client:
        return None
    
    try:
        key = f"{PLANS_CACHE_PREFIX}all"
        data = await client.get(key)
        if data:
            return pickle.loads(data)
    except Exception as e:
        logger.debug(f"Ошибка получения тарифов из кэша: {e}")
    
    return None


async def set_cached_plans(plans_data: Dict[str, Any], ttl: int = CACHE_TTL):
    """Сохраняет тарифы в кэш"""
    client = get_redis_client()
    if not client:
        return
    
    try:
        key = f"{PLANS_CACHE_PREFIX}all"
        data = pickle.dumps(plans_data)
        await client.setex(key, ttl, data)
    except Exception as e:
        logger.debug(f"Ошибка сохранения тарифов в кэш: {e}")


async def get_cache_stats() -> Dict[str, Any]:
    """Получает статистику кэша"""
    client = get_redis_client()
    if not client:
        return {"enabled": False}
    
    try:
        # Подсчитываем ключи кэша
        sub_keys = 0
        user_keys = 0
        cursor = 0
        
        while True:
            cursor, keys = await client.scan(cursor, match=f"{SUBSCRIPTION_CACHE_PREFIX}*", count=100)
            sub_keys += len(keys)
            if cursor == 0:
                break
        
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor, match=f"{USER_CACHE_PREFIX}*", count=100)
            user_keys += len(keys)
            if cursor == 0:
                break
        
        return {
            "enabled": True,
            "subscription_keys": sub_keys,
            "user_keys": user_keys,
            "total_keys": sub_keys + user_keys
        }
    except Exception as e:
        logger.debug(f"Ошибка при получении статистики кэша: {e}")
        return {"enabled": True, "error": str(e)}

