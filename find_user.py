#!/usr/bin/env python3
"""
Скрипт для поиска пользователя в Remna API
"""
import asyncio
import sys
import os

# Добавляем путь к src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from app.remnawave.client import RemnaClient
from app.logger import logger


async def find_user(telegram_id: int, user_id: int = None):
    """Находит пользователя в Remna API"""
    client = RemnaClient()
    
    try:
        print(f"🔍 Ищу пользователя с telegram_id={telegram_id} в Remna API...")
        if user_id:
            print(f"🔍 Также проверяю пользователя с id={user_id}...")
        print(f"📡 Base URL: {client.base_url}")
        print(f"🔑 API Key: {'*' * 20 if client.api_key else 'НЕ НАСТРОЕН'}")
        print()
        
        # Сначала пробуем получить пользователя напрямую по ID (если указан)
        if user_id:
            try:
                print(f"📥 Пробую получить пользователя напрямую по id={user_id}...")
                user_data = await client.request("GET", f"/api/users/{user_id}")
                if user_data:
                    print()
                    print("=" * 80)
                    print(f"✅ НАЙДЕН ПОЛЬЗОВАТЕЛЬ ПО ID!")
                    print("=" * 80)
                    import json
                    # Обрабатываем разные форматы ответа
                    if isinstance(user_data, dict):
                        if 'response' in user_data:
                            user_data = user_data['response']
                        print(json.dumps(user_data, indent=2, default=str))
                        print()
                        print(f"Telegram ID: {user_data.get('telegramId')}")
                        print(f"Username: {user_data.get('username')}")
                        print(f"UUID: {user_data.get('uuid')}")
                        print(f"Expire At: {user_data.get('expireAt')}")
                    else:
                        print(json.dumps(user_data, indent=2, default=str))
                    print("=" * 80)
                    await client.close()
                    return user_data
            except Exception as e:
                print(f"⚠️ Не удалось получить пользователя по id={user_id}: {e}")
                import traceback
                traceback.print_exc()
                print()
        
        # Также пробуем получить по UUID (если знаем)
        # Из скриншота видно, что пользователь может быть создан недавно
        # Попробуем проверить все страницы более тщательно
        
        # Получаем всех пользователей
        page_size = 100
        start = 1
        total_checked = 0
        
        while True:
            try:
                response = await client.request("GET", f"/api/users?size={page_size}&start={start}")
                
                # Обрабатываем разные форматы ответа
                users = []
                if isinstance(response, list):
                    users = response
                elif isinstance(response, dict):
                    response_obj = response.get('response', {})
                    users = response_obj.get('users', 
                        response_obj.get('items', 
                            response.get('items', 
                                response.get('data', []))))
                
                total_checked += len(users)
                page_num = (start - 1) // page_size + 1
                
                print(f"📄 Страница {page_num}: {len(users)} пользователей, всего проверено: {total_checked}")
                
                # Выводим всех пользователей для поиска
                print(f"\n📋 Все пользователи на странице {page_num}:")
                for idx, user_data in enumerate(users):
                    user_telegram_id = user_data.get('telegramId') or user_data.get('telegram_id')
                    username = user_data.get('username')
                    uuid_val = user_data.get('uuid') or user_data.get('id')
                    user_id = user_data.get('id')
                    
                    print(f"  [{idx+1}] uuid={uuid_val}, id={user_id}, telegramId={user_telegram_id}, username={username}")
                    
                    # Ищем по telegramId
                    if user_telegram_id:
                        try:
                            if int(user_telegram_id) == telegram_id:
                                print()
                                print("=" * 80)
                                print(f"✅ НАЙДЕН ПОЛЬЗОВАТЕЛЬ ПО TELEGRAM ID!")
                                print("=" * 80)
                                print(f"UUID: {user_data.get('uuid')}")
                                print(f"ID: {user_data.get('id')}")
                                print(f"Telegram ID: {user_telegram_id}")
                                print(f"Username: {user_data.get('username')}")
                                print(f"Email: {user_data.get('email')}")
                                print(f"Status: {user_data.get('status')}")
                                print(f"Expire At: {user_data.get('expireAt')}")
                                print(f"Страница: {page_num}, Позиция: {idx + 1}")
                                print()
                                print("Полные данные:")
                                import json
                                print(json.dumps(user_data, indent=2, default=str))
                                print("=" * 80)
                                
                                await client.close()
                                return user_data
                        except (ValueError, TypeError):
                            pass
                    
                    # Также проверяем username "dukrmv638"
                    if username == 'dukrmv638':
                        print()
                        print("=" * 80)
                        print(f"⚠️ НАЙДЕН ПОЛЬЗОВАТЕЛЬ С USERNAME 'dukrmv638'!")
                        print("=" * 80)
                        print(f"UUID: {user_data.get('uuid')}")
                        print(f"ID: {user_data.get('id')}")
                        print(f"Telegram ID: {user_data.get('telegramId')}")
                        print(f"Username: {user_data.get('username')}")
                        print(f"Email: {user_data.get('email')}")
                        print(f"Status: {user_data.get('status')}")
                        print(f"Expire At: {user_data.get('expireAt')}")
                        print(f"Страница: {page_num}, Позиция: {idx + 1}")
                        print()
                        print("Полные данные:")
                        import json
                        print(json.dumps(user_data, indent=2, default=str))
                        print("=" * 80)
                        
                        # Если telegramId не совпадает, но username совпадает - это может быть наш пользователь
                        if user_telegram_id != telegram_id:
                            print(f"\n⚠️ ВНИМАНИЕ: telegramId в API ({user_telegram_id}) не совпадает с искомым ({telegram_id})!")
                            print("Возможно, нужно обновить telegramId в Remna API")
                        
                        await client.close()
                        return user_data
                
                # Если список пуст - больше страниц нет
                if not users:
                    break
                
                # Если список меньше page_size - это последняя страница
                if len(users) < page_size:
                    break
                
                start += page_size
                
            except Exception as e:
                print(f"❌ Ошибка на странице {start}: {e}")
                break
        
        print()
        print(f"❌ Пользователь с telegram_id={telegram_id} не найден после проверки {total_checked} пользователей")
        
        await client.close()
        return None
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        await client.close()
        return None


if __name__ == "__main__":
    telegram_id = 5628460233
    user_id = None
    
    if len(sys.argv) > 1:
        telegram_id = int(sys.argv[1])
    if len(sys.argv) > 2:
        user_id = int(sys.argv[2])
    
    result = asyncio.run(find_user(telegram_id, user_id))
    sys.exit(0 if result else 1)
