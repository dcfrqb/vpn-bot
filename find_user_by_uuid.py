#!/usr/bin/env python3
"""
Скрипт для получения пользователя по UUID
Использование: python find_user_by_uuid.py 0f6d3bd0-81c2-4cb2-b088-4d4848fee588
"""
import asyncio
import sys
import os
import json

# Добавляем путь к src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from app.remnawave.client import RemnaClient
from app.logger import logger

async def find_user_by_uuid(uuid: str):
    """Получает пользователя по UUID"""
    client = RemnaClient()
    
    print(f"🔍 Получение пользователя по UUID: {uuid}\n")
    
    try:
        user_data = await client.get_user_by_id(uuid)
        
        print("=" * 80)
        print("✅ ПОЛЬЗОВАТЕЛЬ НАЙДЕН!")
        print("=" * 80)
        
        # Обрабатываем разные форматы ответа
        if isinstance(user_data, dict):
            if 'response' in user_data:
                user_data = user_data['response']
            
            print(f"\n📋 Основная информация:")
            print(f"   UUID: {user_data.get('uuid')}")
            print(f"   ID: {user_data.get('id')}")
            print(f"   Telegram ID: {user_data.get('telegramId') or user_data.get('telegram_id')}")
            print(f"   Username: {user_data.get('username')}")
            print(f"   Email: {user_data.get('email')}")
            print(f"   Status: {user_data.get('status')}")
            print(f"   Expire At: {user_data.get('expireAt')}")
            print(f"   Active: {user_data.get('active')}")
            
            subscription_url = (
                user_data.get('subscriptionUrl') or 
                user_data.get('subscription_url')
            )
            subscription_token = (
                user_data.get('subscriptionToken') or 
                user_data.get('subscription_token')
            )
            
            print(f"\n🔗 Подписка:")
            print(f"   Subscription URL: {subscription_url}")
            print(f"   Subscription Token: {subscription_token}")
            
            print(f"\n📋 Полные данные пользователя:")
            print(json.dumps(user_data, indent=2, default=str))
            
        else:
            print(json.dumps(user_data, indent=2, default=str))
        
        print("=" * 80)
        
        await client.close()
        return user_data
        
    except Exception as e:
        print(f"❌ Ошибка при получении пользователя: {e}")
        import traceback
        traceback.print_exc()
        await client.close()
        return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python find_user_by_uuid.py <uuid>")
        print("Пример: python find_user_by_uuid.py 0f6d3bd0-81c2-4cb2-b088-4d4848fee588")
        sys.exit(1)
    
    uuid = sys.argv[1]
    result = asyncio.run(find_user_by_uuid(uuid))
    sys.exit(0 if result else 1)
