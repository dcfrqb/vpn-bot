"""Репозиторий для работы с подписками"""
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.db.models import Subscription
from app.logger import logger


class SubscriptionRepo:
    """Репозиторий для работы с подписками"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_subscription_by_user_id(self, telegram_user_id: int) -> Optional[Subscription]:
        """Получить подписку пользователя (активную или последнюю)"""
        result = await self.session.execute(
            select(Subscription)
            .where(Subscription.telegram_user_id == telegram_user_id)
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
    
    async def upsert_subscription(
        self,
        telegram_user_id: int,
        defaults: Dict[str, Any]
    ) -> Subscription:
        """
        Upsert подписки для пользователя.
        Если у пользователя уже есть подписка - обновляет её.
        Если нет - создает новую.
        
        Args:
            telegram_user_id: Telegram ID пользователя
            defaults: Словарь с полями подписки:
                - remna_user_id: str (опционально)
                - plan_code: str (обязательно)
                - plan_name: str (опционально)
                - active: bool (обязательно)
                - valid_until: datetime (опционально)
                - config_data: dict (опционально)
                - remna_subscription_id: str (опционально)
        
        Returns:
            Subscription (созданная или обновленная)
        """
        now = datetime.utcnow()
        update_data = defaults.copy()
        update_data.setdefault('updated_at', now)
        
        # Получаем существующую подписку
        existing = await self.get_subscription_by_user_id(telegram_user_id)
        
        if existing:
            # Обновляем существующую подписку
            for key, value in update_data.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            
            await self.session.flush()
            await self.session.refresh(existing)
            logger.debug(f"Обновлена подписка {existing.id} для пользователя {telegram_user_id}")
            return existing
        else:
            # Создаем новую подписку
            if 'created_at' not in update_data:
                update_data['created_at'] = now
            
            subscription = Subscription(
                telegram_user_id=telegram_user_id,
                **update_data
            )
            self.session.add(subscription)
            await self.session.flush()
            await self.session.refresh(subscription)
            logger.debug(f"Создана подписка {subscription.id} для пользователя {telegram_user_id}")
            return subscription
