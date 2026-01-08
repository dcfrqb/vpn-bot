from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable, Union
from datetime import datetime, timedelta
import asyncio
from app.config import settings
from app.logger import logger
from app.services.users import update_user_activity


# Кэш для отслеживания последнего обновления активности (TTL 5 минут)
_activity_cache: Dict[int, datetime] = {}
_activity_cache_lock = asyncio.Lock()
ACTIVITY_UPDATE_INTERVAL = timedelta(minutes=5)  # Обновляем активность не чаще раза в 5 минут


class AuthMiddleware(BaseMiddleware):
    """Middleware для проверки прав доступа пользователей и обновления активности"""
    
    async def __call__(
        self,
        handler: Callable[[Union[Message, CallbackQuery], Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any]
    ) -> Any:
        """Проверяет права доступа пользователя и обновляет активность"""
        
        user_id = event.from_user.id if event.from_user else None
        
        if not user_id:
            logger.warning("Получено сообщение без информации о пользователе")
            return await handler(event, data)
        
        # Оптимизация: обновляем активность не чаще раза в 5 минут
        async with _activity_cache_lock:
            now = datetime.utcnow()
            last_update = _activity_cache.get(user_id)
            
            if not last_update or (now - last_update) >= ACTIVITY_UPDATE_INTERVAL:
                # Обновляем в фоне, не блокируем handler
                async def update_activity_background():
                    try:
                        await update_user_activity(user_id)
                        async with _activity_cache_lock:
                            _activity_cache[user_id] = now
                    except Exception as e:
                        logger.debug(f"Ошибка обновления активности пользователя {user_id}: {e}")
                
                asyncio.create_task(update_activity_background())
                _activity_cache[user_id] = now
        
        is_admin = user_id in settings.ADMINS
        
        data["is_admin"] = is_admin
        data["user_id"] = user_id
        
        username = event.from_user.username if event.from_user else "Unknown"
        logger.debug(f"Пользователь {user_id} (@{username}) - {'Admin' if is_admin else 'User'}")
        
        return await handler(event, data)
