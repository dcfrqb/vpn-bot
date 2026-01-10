#!/usr/bin/env python3
"""
Скрипт для поиска пользователя по токену подписки
Использование: python find_user_by_subscription.py Tkk_d3RFXAxPGE3Z
"""
import asyncio
import sys
import os

# Добавляем путь к src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from app.remnawave.client import RemnaClient
from app.config import settings
from app.logger import logger

async def find_user_by_subscription_token(subscription_token: str):
    """Ищет пользователя по токену подписки"""
    client = RemnaClient()
    
    print(f"🔍 Поиск пользователя по токену подписки: {subscription_token}")
    print(f"📋 Ожидаемая ссылка: https://sub.crs-projects.com/{subscription_token}\n")
    
    page_size = 100
    start = 1
    total_checked = 0
    max_pages = 50
    found_users = []
    
    while start <= max_pages * page_size:
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
            
            # Ищем пользователя по subscriptionToken
            for idx, user_data in enumerate(users):
                user_token = (
                    user_data.get('subscriptionToken') or 
                    user_data.get('subscription_token') or
                    user_data.get('token')
                )
                
                # Также проверяем subscriptionUrl
                user_url = (
                    user_data.get('subscriptionUrl') or 
                    user_data.get('subscription_url')
                )
                
                # Извлекаем токен из URL если есть (формат: https://sub.crs-projects.com/TOKEN)
                if user_url and not user_token:
                    import re
                    # Извлекаем токен из URL
                    match = re.search(r'/([^/]+)$', user_url)
                    if match:
                        user_token = match.group(1)
                
                # Также проверяем в response объекте
                if not user_token:
                    response_obj = user_data.get('response', {})
                    if isinstance(response_obj, dict):
                        user_token = (
                            response_obj.get('subscriptionToken') or 
                            response_obj.get('subscription_token')
                        )
                        user_url = (
                            response_obj.get('subscriptionUrl') or 
                            response_obj.get('subscription_url')
                        )
                        if user_url and not user_token:
                            import re
                            match = re.search(r'/([^/]+)$', user_url)
                            if match:
                                user_token = match.group(1)
                
                # Проверяем совпадение (также проверяем частичное совпадение в URL)
                found = False
                if user_token and user_token == subscription_token:
                    found = True
                elif user_url and subscription_token in user_url:
                    found = True
                
                if found:
                    uuid = user_data.get('uuid') or user_data.get('id') or user_data.get('_id')
                    telegram_id = user_data.get('telegramId') or user_data.get('telegram_id')
                    username = user_data.get('username')
                    
                    print(f"\n✅ НАЙДЕН ПОЛЬЗОВАТЕЛЬ!")
                    print(f"   UUID: {uuid}")
                    print(f"   Telegram ID: {telegram_id}")
                    print(f"   Username: {username}")
                    print(f"   Страница: {page_num}, позиция: {idx+1}")
                    print(f"\n📋 Полные данные пользователя:")
                    print(f"   {user_data}")
                    
                    found_users.append({
                        'uuid': uuid,
                        'telegram_id': telegram_id,
                        'username': username,
                        'page': page_num,
                        'position': idx + 1,
                        'data': user_data
                    })
            
            # Если список пуст - больше страниц нет
            if not users:
                print(f"📄 Страница {page_num} пуста, завершаю поиск")
                break
            
            # Если список меньше page_size - это последняя страница
            if len(users) < page_size:
                print(f"📄 Страница {page_num} содержит {len(users)} пользователей (меньше {page_size}), это последняя страница")
                break
            
            start += page_size
            
        except Exception as e:
            print(f"❌ Ошибка на странице {start}: {e}")
            import traceback
            traceback.print_exc()
            break
    
    if not found_users:
        print(f"\n❌ Пользователь с токеном подписки '{subscription_token}' не найден после проверки {total_checked} пользователей")
        print(f"💡 Попробуйте проверить:")
        print(f"   1. Правильность токена в ссылке")
        print(f"   2. Существует ли пользователь в Remna панели")
        print(f"   3. Есть ли у пользователя активная подписка")
    else:
        print(f"\n✅ Найдено пользователей: {len(found_users)}")
        for i, user in enumerate(found_users, 1):
            print(f"\n   [{i}] UUID: {user['uuid']}, Telegram ID: {user['telegram_id']}, Username: {user['username']}")
    
    await client.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python find_user_by_subscription.py <subscription_token>")
        print("Пример: python find_user_by_subscription.py Tkk_d3RFXAxPGE3Z")
        sys.exit(1)
    
    token = sys.argv[1]
    asyncio.run(find_user_by_subscription_token(token))
