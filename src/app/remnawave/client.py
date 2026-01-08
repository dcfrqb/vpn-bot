import asyncio
import httpx
from typing import Optional, Dict, Any
from app.config import settings
from app.logger import logger


class RemnaClient:
    def __init__(self, max_retries: int = 3, initial_delay: float = 1.0, max_delay: float = 60.0):
        """Инициализирует клиент Remna API"""
        base_url = settings.remna_base_url or settings.REMNA_API_BASE
        self.base_url = str(base_url).rstrip("/") if base_url else None
        self.api_key = settings.remna_api_token or settings.REMNA_API_KEY
        self.client = httpx.AsyncClient(timeout=30.0)
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay

    async def request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Выполняет HTTP запрос к API Remna с использованием API токена и retry механизмом"""
        if not self.api_key:
            raise ValueError("REMNA_API_KEY не настроен в конфигурации")

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.api_key}"
        headers["Content-Type"] = "application/json"

        if endpoint.startswith("/api") and self.base_url and self.base_url.rstrip("/").endswith("/api"):
            endpoint = endpoint[4:]
        
        url = f"{self.base_url}{endpoint}"
        
        last_exception = None
        delay = self.initial_delay
        
        for attempt in range(self.max_retries + 1):
            try:
                if attempt > 0:
                    logger.warning(f"Повторная попытка {attempt}/{self.max_retries} для {method} {url} (задержка: {delay:.2f}с)")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self.max_delay)
                else:
                    logger.debug(f"Выполняется {method} запрос к {url}")
                
                resp = await self.client.request(method, url, headers=headers, **kwargs)
                resp.raise_for_status()
                
                if attempt > 0:
                    logger.info(f"Запрос успешно выполнен после {attempt} попыток")
                
                return resp.json()
                
            except httpx.HTTPStatusError as e:
                last_exception = e
                status_code = e.response.status_code
                if 400 <= status_code < 500:
                    if status_code == 429:
                        logger.warning(f"Rate limit достигнут (429), повтор через {delay:.2f}с")
                        if attempt < self.max_retries:
                            continue
                    logger.error(f"HTTP ошибка {status_code}: {e.response.text}")
                    raise
                if attempt < self.max_retries:
                    logger.warning(f"HTTP ошибка {status_code}, повтор через {delay:.2f}с")
                    continue
                else:
                    logger.error(f"HTTP ошибка {status_code} после {self.max_retries} попыток: {e.response.text}")
                    raise
                    
            except httpx.RequestError as e:
                last_exception = e
                if attempt < self.max_retries:
                    logger.warning(f"Ошибка запроса (сеть/таймаут), повтор через {delay:.2f}с: {e}")
                    continue
                else:
                    logger.error(f"Ошибка запроса после {self.max_retries} попыток: {e}")
                    raise
        
        if last_exception:
            raise last_exception
        raise RuntimeError("Неожиданная ошибка в retry механизме")

    async def close(self):
        """Закрывает HTTP клиент"""
        await self.client.aclose()

    async def get_api_tokens(self) -> Dict[str, Any]:
        """Получить список API токенов"""
        return await self.request("GET", "/api/tokens")

    async def create_api_token(self, name: str, permissions: Optional[list] = None) -> Dict[str, Any]:
        """Создать новый API токен"""
        payload = {"name": name}
        if permissions:
            payload["permissions"] = permissions
        return await self.request("POST", "/api/tokens", json=payload)

    async def delete_api_token(self, token_id: str) -> Dict[str, Any]:
        """Удалить API токен"""
        return await self.request("DELETE", f"/api/tokens/{token_id}")

    async def update_api_token(self, token_id: str, name: Optional[str] = None, 
                              permissions: Optional[list] = None) -> Dict[str, Any]:
        """Обновить API токен"""
        payload = {}
        if name is not None:
            payload["name"] = name
        if permissions is not None:
            payload["permissions"] = permissions
        return await self.request("PUT", f"/api/tokens/{token_id}", json=payload)

    async def get_users(self, size: int = 50, start: int = 1) -> Dict[str, Any]:
        """Получить список пользователей"""
        return await self.request("GET", f"/api/users?size={size}&start={start}")

    async def create_user(self, username: str, password: str, expire_at: Optional[str] = None, telegram_id: Optional[int] = None, active_internal_squads: Optional[list] = None) -> Dict[str, Any]:
        """Создать нового пользователя через API"""
        # Используем /api/users для создания через API токен (не /api/auth/register)
        # Remna API требует поле expireAt (дата истечения подписки)
        payload = {"username": username, "password": password}
        if expire_at:
            payload["expireAt"] = expire_at
        if telegram_id:
            payload["telegramId"] = int(telegram_id)  # Remna API принимает telegramId как число
        if active_internal_squads:
            payload["activeInternalSquads"] = active_internal_squads
        return await self.request("POST", "/api/users", json=payload)

    async def delete_user(self, user_id: str) -> Dict[str, Any]:
        """Удалить пользователя"""
        return await self.request("DELETE", f"/api/users/{user_id}")

    async def get_user_by_id(self, user_id: str) -> Dict[str, Any]:
        """Получить пользователя по ID"""
        return await self.request("GET", f"/api/users/{user_id}")

    async def update_user(self, user_id: str, **kwargs) -> Dict[str, Any]:
        """Обновить данные пользователя"""
        # Пробуем разные варианты эндпоинтов
        endpoints = [
            f"/api/user/{user_id}",
            f"/api/users/{user_id}",
            f"/api/users/{user_id}/update",
        ]
        
        for endpoint in endpoints:
            try:
                return await self.request("PUT", endpoint, json=kwargs)
            except Exception as e:
                if "404" not in str(e):
                    # Если не 404, значит эндпоинт существует, но другая ошибка
                    raise
                continue
        
        # Если все варианты не сработали, пробуем PATCH
        for endpoint in endpoints:
            try:
                return await self.request("PATCH", endpoint, json=kwargs)
            except Exception as e:
                if "404" not in str(e):
                    raise
                continue
        
        # Если ничего не сработало, пробуем последний вариант
        return await self.request("PUT", f"/api/users/{user_id}", json=kwargs)

    async def get_nodes(self) -> Dict[str, Any]:
        """Получить список нод"""
        return await self.request("GET", "/api/nodes")

    async def get_internal_squads(self) -> Dict[str, Any]:
        """Получить список внутренних сквадов"""
        return await self.request("GET", "/api/internal-squads")

    async def get_squad_by_name(self, squad_name: str) -> Optional[Dict[str, Any]]:
        """Получить сквад по имени"""
        try:
            response = await self.get_internal_squads()
            squads = []
            
            # Обрабатываем разные форматы ответа
            if isinstance(response, list):
                squads = response
            elif isinstance(response, dict):
                # Проверяем response.internalSquads (основной формат)
                response_obj = response.get('response', {})
                squads = response_obj.get('internalSquads', 
                    response_obj.get('items', 
                        response.get('items', 
                            response.get('data', []))))
            
            for squad in squads:
                if squad.get('name') == squad_name:
                    return squad
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении сквада {squad_name}: {e}")
            return None

    async def get_user_subscription_url(self, user_id: str) -> Optional[str]:
        """Получить subscription URL для пользователя
        
        API Remnawave возвращает структуру:
        {
            "response": {
                "uuid": "...",
                "subscriptionUrl": "https://...",
                "subscriptionToken": "...",
                ...
            }
        }
        или может быть напрямую объект пользователя
        """
        try:
            user_data = await self.get_user_by_id(user_id)
            logger.debug(f"Получены данные пользователя {user_id}: {list(user_data.keys()) if isinstance(user_data, dict) else type(user_data)}")
            
            # Проверяем разные варианты структуры ответа
            subscription_url = None
            subscription_token = None
            
            # Вариант 1: Прямо в корне ответа
            if isinstance(user_data, dict):
                subscription_url = user_data.get('subscriptionUrl') or user_data.get('subscription_url')
                subscription_token = user_data.get('subscriptionToken') or user_data.get('subscription_token')
                
                # Вариант 2: В response объекте
                if not subscription_url and not subscription_token:
                    response = user_data.get('response', {})
                    if isinstance(response, dict):
                        subscription_url = response.get('subscriptionUrl') or response.get('subscription_url')
                        subscription_token = response.get('subscriptionToken') or response.get('subscription_token')
                        
                        # Также проверяем вложенные структуры
                        if not subscription_url and not subscription_token:
                            # Может быть в data
                            data = response.get('data', {})
                            if isinstance(data, dict):
                                subscription_url = data.get('subscriptionUrl') or data.get('subscription_url')
                                subscription_token = data.get('subscriptionToken') or data.get('subscription_token')
                
                # Вариант 3: В raw_data (если есть)
                if not subscription_url and not subscription_token:
                    raw_data = user_data.get('raw_data', {})
                    if isinstance(raw_data, dict):
                        subscription_url = raw_data.get('subscriptionUrl') or raw_data.get('subscription_url')
                        subscription_token = raw_data.get('subscriptionToken') or raw_data.get('subscription_token')
            
            # Если нашли subscription_url напрямую
            if subscription_url:
                # Убеждаемся, что используется .com домен (заменяем .ru на .com если встретили)
                if "sub.crs-projects.ru" in subscription_url:
                    subscription_url = subscription_url.replace("sub.crs-projects.ru", "sub.crs-projects.com")
                    logger.info(f"Заменен домен на .com для пользователя {user_id}")
                logger.info(f"Найден subscriptionUrl для пользователя {user_id}: {subscription_url[:50]}...")
                return subscription_url
            
            # Если нашли token, формируем URL
            if subscription_token:
                subscription_url = f"https://sub.crs-projects.com/{subscription_token}"
                logger.info(f"Сформирован subscriptionUrl из token для пользователя {user_id}: {subscription_url[:50]}...")
                return subscription_url
            
            # Если ничего не нашли, логируем структуру для отладки
            logger.warning(f"Subscription URL не найден для пользователя {user_id}")
            logger.debug(f"Структура ответа: {user_data}")
            return None
            
        except Exception as e:
            logger.error(f"Ошибка при получении subscription URL для пользователя {user_id}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None

    async def health_check(self) -> Dict[str, Any]:
        """Проверить статус API"""
        return await self.request("GET", "/api/system/health")