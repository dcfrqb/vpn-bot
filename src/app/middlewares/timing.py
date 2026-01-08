"""Middleware для измерения времени выполнения handlers"""
import time
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable, Union
from app.logger import logger


class TimingMiddleware(BaseMiddleware):
    """Middleware для логирования времени выполнения handlers"""
    
    # Порог для логирования медленных операций (в секундах)
    SLOW_HANDLER_THRESHOLD = 1.0
    
    async def __call__(
        self,
        handler: Callable[[Union[Message, CallbackQuery], Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any]
    ) -> Any:
        """Измеряет время выполнения handler"""
        
        start_time = time.time()
        handler_name = handler.__name__ if hasattr(handler, '__name__') else 'unknown'
        
        try:
            result = await handler(event, data)
            elapsed = time.time() - start_time
            
            # Логируем только медленные handlers
            if elapsed > self.SLOW_HANDLER_THRESHOLD:
                user_id = event.from_user.id if event.from_user else None
                event_type = "callback" if isinstance(event, CallbackQuery) else "message"
                logger.warning(
                    f"⚠️ Медленный handler: {handler_name} "
                    f"({event_type}, user={user_id}) занял {elapsed:.3f}с"
                )
            
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            user_id = event.from_user.id if event.from_user else None
            logger.error(
                f"❌ Ошибка в handler {handler_name} (user={user_id}) после {elapsed:.3f}с: {e}"
            )
            raise

