"""Репозиторий для работы с пользователями"""
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TelegramUser, RemnaUser
from app.logger import logger


class UserRepo:
    """Репозиторий для работы с пользователями"""
    
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[TelegramUser]:
        """Получить пользователя по telegram_id"""
        result = await self.session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()
    
    async def upsert_user_by_telegram_id(
        self,
        telegram_id: int,
        defaults: Dict[str, Any]
    ) -> TelegramUser:
        """
        Upsert пользователя по telegram_id.
        Использует ON CONFLICT для безопасного обновления/создания.
        
        Args:
            telegram_id: Telegram ID пользователя
            defaults: Словарь с полями для обновления/создания
                Может содержать: username, first_name, last_name, language_code,
                remna_user_id, is_admin, last_activity_at
        
        Returns:
            TelegramUser (созданный или обновленный)
        """
        # Подготавливаем данные
        now = datetime.utcnow()
        update_data = defaults.copy()
        update_data.setdefault('updated_at', now)
        
        # Если создаем нового пользователя
        if 'created_at' not in update_data:
            update_data['created_at'] = now
        
        # Если обновляем существующего
        if 'last_activity_at' not in update_data:
            update_data['last_activity_at'] = now
        
        # Используем простой подход: получаем или создаем
        user = await self.get_user_by_telegram_id(telegram_id)
        
        if user:
            # Обновляем существующего пользователя
            for key, value in update_data.items():
                if hasattr(user, key):
                    setattr(user, key, value)
            await self.session.flush()
        else:
            # Создаем нового пользователя
            user = TelegramUser(
                telegram_id=telegram_id,
                **update_data
            )
            self.session.add(user)
            await self.session.flush()
            await self.session.refresh(user)
        
        logger.debug(f"Upsert пользователя {telegram_id}: remna_user_id={user.remna_user_id}")
        return user
    
    async def upsert_remna_user(
        self,
        remna_id: str,
        defaults: Dict[str, Any]
    ) -> RemnaUser:
        """
        Upsert пользователя Remna по remna_id.
        
        Args:
            remna_id: UUID пользователя в Remna
            defaults: Словарь с полями (username, email, raw_data, last_synced_at)
        
        Returns:
            RemnaUser
        """
        now = datetime.utcnow()
        update_data = defaults.copy()
        update_data.setdefault('updated_at', now)
        update_data.setdefault('last_synced_at', now)
        
        if 'created_at' not in update_data:
            update_data['created_at'] = now
        
        # Используем простой подход: получаем или создаем
        result = await self.session.execute(
            select(RemnaUser).where(RemnaUser.remna_id == remna_id)
        )
        remna_user = result.scalar_one_or_none()
        
        if remna_user:
            # Обновляем существующего пользователя
            for key, value in update_data.items():
                if hasattr(remna_user, key):
                    setattr(remna_user, key, value)
            await self.session.flush()
        else:
            # Создаем нового пользователя
            remna_user = RemnaUser(
                remna_id=remna_id,
                **update_data
            )
            self.session.add(remna_user)
            await self.session.flush()
        
        await self.session.refresh(remna_user)
        return remna_user
