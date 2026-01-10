#!/usr/bin/env python3
"""
Скрипт для сохранения remna_user_id в БД
Использование: python save_remna_uuid.py <telegram_id> <remna_uuid>
"""
import asyncio
import sys
import os

# Добавляем путь к src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from app.db.session import SessionLocal
from app.db.models import TelegramUser, RemnaUser
from app.remnawave.client import RemnaClient
from sqlalchemy import select
from datetime import datetime
from app.logger import logger

async def save_remna_uuid(telegram_id: int, remna_uuid: str):
    """Сохраняет remna_user_id для пользователя"""
    if not SessionLocal:
        print("❌ SessionLocal не инициализирован")
        return False
    
    async with SessionLocal() as session:
        try:
            # Получаем пользователя из Remna API для получения полных данных
            client = RemnaClient()
            user_data = await client.get_user_by_id(remna_uuid)
            await client.close()
            
            # Обрабатываем ответ
            if isinstance(user_data, dict):
                if 'response' in user_data:
                    user_data = user_data['response']
            
            username = user_data.get('username')
            email = user_data.get('email')
            
            print(f"📋 Данные из Remna API:")
            print(f"   UUID: {remna_uuid}")
            print(f"   Username: {username}")
            print(f"   Email: {email}")
            print(f"   Telegram ID: {user_data.get('telegramId')}")
            print()
            
            # Создаем или обновляем RemnaUser
            remna_user_result = await session.execute(
                select(RemnaUser).where(RemnaUser.remna_id == remna_uuid)
            )
            remna_user = remna_user_result.scalar_one_or_none()
            
            if remna_user:
                print(f"✅ RemnaUser уже существует, обновляю...")
                remna_user.username = username
                remna_user.email = email
                remna_user.raw_data = user_data
                remna_user.updated_at = datetime.utcnow()
                remna_user.last_synced_at = datetime.utcnow()
            else:
                print(f"📝 Создаю новый RemnaUser...")
                remna_user = RemnaUser(
                    remna_id=remna_uuid,
                    username=username,
                    email=email,
                    raw_data=user_data,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                    last_synced_at=datetime.utcnow()
                )
                session.add(remna_user)
            
            await session.flush()
            
            # Обновляем TelegramUser
            user_result = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
            )
            telegram_user = user_result.scalar_one_or_none()
            
            if telegram_user:
                print(f"✅ TelegramUser найден, обновляю remna_user_id...")
                old_remna_user_id = telegram_user.remna_user_id
                telegram_user.remna_user_id = remna_uuid
                telegram_user.updated_at = datetime.utcnow()
                
                if old_remna_user_id != remna_uuid:
                    print(f"   Старый remna_user_id: {old_remna_user_id}")
                    print(f"   Новый remna_user_id: {remna_uuid}")
                else:
                    print(f"   remna_user_id уже был установлен: {remna_uuid}")
            else:
                print(f"⚠️ TelegramUser с telegram_id={telegram_id} не найден в БД")
                print(f"   Создаю нового TelegramUser...")
                telegram_user = TelegramUser(
                    telegram_id=telegram_id,
                    remna_user_id=remna_uuid,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                session.add(telegram_user)
            
            await session.commit()
            
            print()
            print("=" * 80)
            print("✅ УСПЕШНО!")
            print("=" * 80)
            print(f"   Telegram ID: {telegram_id}")
            print(f"   Remna UUID: {remna_uuid}")
            print(f"   Username: {username}")
            print()
            print("Теперь бот сможет находить этого пользователя напрямую по UUID!")
            print("=" * 80)
            
            return True
            
        except Exception as e:
            await session.rollback()
            print(f"❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
            return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python save_remna_uuid.py <telegram_id> <remna_uuid>")
        print("Пример: python save_remna_uuid.py 5628460233 0f6d3bd0-81c2-4cb2-b088-4d4848fee588")
        sys.exit(1)
    
    telegram_id = int(sys.argv[1])
    remna_uuid = sys.argv[2]
    
    result = asyncio.run(save_remna_uuid(telegram_id, remna_uuid))
    sys.exit(0 if result else 1)
