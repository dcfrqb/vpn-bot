import asyncio
import httpx
from typing import Optional, Dict, Any, Union
from datetime import datetime, date, timezone
from dataclasses import dataclass
from app.config import settings
from app.core.errors import InfraError
from app.logger import logger

# Фиксированное значение для подписки "навсегда" (Remna не поддерживает null/бессрочно)
LIFETIME_EXPIRE_AT = "2099-12-31T23:59:59Z"

# Whitelist полей для update_user и маппинг snake_case -> camelCase
_USER_UPDATE_WHITELIST = {
    "name": "name",
    "username": "username",
    "password": "password",
    "permissions": "permissions",
    "expire_at": "expireAt",
    "expireAt": "expireAt",
    "telegram_id": "telegramId",
    "telegramId": "telegramId",
    "active_internal_squads": "activeInternalSquads",
    "activeInternalSquads": "activeInternalSquads",
}


def normalize_expire_at(value: Optional[Union[str, datetime, date]]) -> Optional[str]:
    """
    Нормализует значение expireAt для Remna API.
    - None -> None
    - datetime/date с year >= 2099 -> LIFETIME_EXPIRE_AT
    - str содержащий "2099" (подписка навсегда) -> LIFETIME_EXPIRE_AT
    - иначе -> ISO8601 UTC с суффиксом Z
    """
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        if getattr(value, "year", 0) >= 2099:
            return LIFETIME_EXPIRE_AT
        if isinstance(value, datetime):
            if value.tzinfo is None:
                logger.debug("Remna normalize_expire_at: naive datetime трактуется как UTC")
                value = value.replace(tzinfo=timezone.utc)
            value = value.astimezone(timezone.utc)
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        return datetime(value.year, value.month, value.day, 23, 59, 59, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, str):
        if "2099" in value:
            return LIFETIME_EXPIRE_AT
        try:
            expire_str = value.replace("Z", "+00:00").replace("z", "+00:00")
            if "+" not in expire_str and "-" not in expire_str[-6:]:
                expire_str += "+00:00"
            dt = datetime.fromisoformat(expire_str)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc)
            else:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            return value
    return None


def build_user_payload_from_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Строит payload для Remna API из kwargs с whitelist и маппингом snake_case -> camelCase.
    Не включает поля со значением None. Приоритет для expire: expireAt > expire_at.
    """
    result: Dict[str, Any] = {}
    expire_val = kwargs.get("expireAt", kwargs.get("expire_at"))
    if expire_val is not None:
        normalized = normalize_expire_at(expire_val)
        if normalized is not None:
            result["expireAt"] = normalized
    for key, val in kwargs.items():
        if val is None:
            continue
        if key in ("expire_at", "expireAt"):
            continue
        api_key = _USER_UPDATE_WHITELIST.get(key)
        if api_key is None:
            logger.debug(f"Remna update_user: игнорируем неподдерживаемое поле {key!r}")
            continue
        if api_key == "telegramId":
            result["telegramId"] = int(val)
        elif api_key == "activeInternalSquads":
            result["activeInternalSquads"] = val if isinstance(val, list) else [val]
        else:
            result[api_key] = val
    return result


@dataclass
class RemnaUser:
    """DTO для пользователя Remna"""
    uuid: str  # remna_id
    telegram_id: Optional[int]
    username: Optional[str]
    name: Optional[str]  # display name или username
    raw_data: Dict[str, Any]  # полные данные из API


@dataclass
class RemnaSubscription:
    """DTO для подписки Remna"""
    active: bool
    expires_at: Optional[datetime]  # expireAt из API
    plan: Optional[str]  # тариф, если доступен
    raw_data: Dict[str, Any]  # полные данные из API


# Глобальный переиспользуемый HTTP клиент для connection pooling
_shared_http_client: Optional[httpx.AsyncClient] = None


def get_shared_http_client() -> httpx.AsyncClient:
    """Получает переиспользуемый HTTP клиент с connection pooling"""
    global _shared_http_client
    if _shared_http_client is None:
        # Используем connection pooling для лучшей производительности
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
        _shared_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),  # 30s общий, 10s на подключение
            limits=limits
            # HTTP/2 отключен (требует пакет h2, не критично для работы)
        )
        logger.info("Создан переиспользуемый HTTP клиент для Remna API с connection pooling")
    return _shared_http_client


async def close_shared_http_client():
    """Закрывает глобальный HTTP клиент (для cleanup)"""
    global _shared_http_client
    if _shared_http_client:
        await _shared_http_client.aclose()
        _shared_http_client = None
        logger.info("Закрыт переиспользуемый HTTP клиент для Remna API")


class RemnaClient:
    def __init__(self, max_retries: int = 3, initial_delay: float = 1.0, max_delay: float = 60.0, 
                 use_shared_client: bool = True):
        """
        Инициализирует клиент Remna API
        
        Args:
            max_retries: Максимальное количество повторных попыток
            initial_delay: Начальная задержка между попытками (секунды)
            max_delay: Максимальная задержка между попытками (секунды)
            use_shared_client: Использовать ли переиспользуемый HTTP клиент (connection pooling)
        """
        base_url = settings.remna_base_url or settings.REMNA_API_BASE
        self.base_url = str(base_url).rstrip("/") if base_url else None
        self.api_key = settings.remna_api_token or settings.REMNA_API_KEY
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.use_shared_client = use_shared_client
        self._own_client: Optional[httpx.AsyncClient] = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        """Получает HTTP клиент (переиспользуемый или собственный)"""
        if self.use_shared_client:
            return get_shared_http_client()
        else:
            if self._own_client is None:
                self._own_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
                )
            return self._own_client

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
                    # Используем InfraError для сетевых ошибок и таймаутов
                    raise InfraError(
                        message=f"Ошибка подключения к Remna API: {str(e)}",
                        service="remna",
                        details=f"URL: {url}, Attempts: {self.max_retries + 1}"
                    ) from e
        
        if last_exception:
            raise InfraError(
                message=f"Ошибка подключения к Remna API после {self.max_retries + 1} попыток",
                service="remna",
                details=str(last_exception)
            ) from last_exception
        raise RuntimeError("Неожиданная ошибка в retry механизме")

    async def close(self):
        """Закрывает HTTP клиент (только если это собственный клиент)"""
        if not self.use_shared_client and self._own_client:
            await self._own_client.aclose()
            self._own_client = None

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

    async def create_user(self, username: str, password: str, expire_at: Optional[Union[str, datetime, date]] = None, telegram_id: Optional[int] = None, active_internal_squads: Optional[list] = None) -> Dict[str, Any]:
        """Создать нового пользователя через API"""
        # Remna API требует поле expireAt (camelCase). Используем normalize_expire_at для единообразия.
        payload = {"username": username, "password": password}
        normalized_expire = normalize_expire_at(expire_at)
        if normalized_expire:
            payload["expireAt"] = normalized_expire
            logger.debug(f"Remna create_user: expireAt={normalized_expire}")
        if telegram_id:
            payload["telegramId"] = int(telegram_id)
        if active_internal_squads:
            payload["activeInternalSquads"] = active_internal_squads
        return await self.request("POST", "/api/users", json=payload)

    async def get_or_create_user(self, telegram_id: int, name: str, expire_at: Optional[str] = None) -> RemnaUser:
        """
        Получить или создать пользователя в Remna.

        Логика:
        1. Попробовать найти по telegram_id
        2. Если не найден — создать нового
        3. Если username занят — найти по username и добавить telegramId

        Args:
            telegram_id: Telegram ID пользователя
            name: Имя пользователя
            expire_at: Дата истечения (по умолчанию: +2 дня, trial)

        Returns:
            RemnaUser
        """
        import secrets
        from datetime import timedelta

        username = f"tg_{telegram_id}"

        # 1. Сначала ищем по telegram_id
        existing = await self.get_user_by_telegram_id(telegram_id)
        if existing:
            logger.info(f"Найден существующий пользователь: uuid={existing.uuid}, telegram_id={telegram_id}")
            return existing

        # 2. Не найден — создаём
        if not expire_at:
            expire_at = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

        password = secrets.token_urlsafe(16)

        try:
            response = await self.create_user(
                username=username,
                password=password,
                expire_at=expire_at,
                telegram_id=telegram_id
            )

            user_data = response.get('response', response) if isinstance(response, dict) else response
            uuid = user_data.get('uuid') or user_data.get('id')
            if not uuid:
                raise ValueError(f"Не удалось получить uuid из ответа: {response}")

            logger.info(f"Создан пользователь Remna: uuid={uuid}, telegram_id={telegram_id}")
            return RemnaUser(
                uuid=str(uuid),
                telegram_id=telegram_id,
                username=username,
                name=name,
                raw_data=user_data
            )

        except httpx.HTTPStatusError as e:
            # 3. Username уже занят — ищем по username и добавляем telegramId
            if e.response.status_code in (400, 409) and 'exists' in e.response.text.lower():
                logger.warning(f"Username {username} занят, ищем пользователя и добавляем telegramId")
                try:
                    found = await self._find_user_by_username(username)
                    if found:
                        uuid = found.get('uuid') or found.get('id')
                        if uuid:
                            # Обновляем telegramId
                            await self.update_user(uuid, telegramId=telegram_id)
                            logger.info(f"Обновлён telegramId для пользователя {uuid}")
                            return RemnaUser(
                                uuid=str(uuid),
                                telegram_id=telegram_id,
                                username=username,
                                name=name,
                                raw_data=found
                            )
                except Exception as update_err:
                    logger.error(f"Не удалось обновить telegramId: {update_err}")

            logger.error(f"Ошибка создания пользователя {telegram_id}: {e.response.status_code}")
            raise

    async def _find_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Найти пользователя по username (одна страница, быстрый поиск)."""
        try:
            response = await self.request("GET", f"/api/users?size=100&start=1")
            users = []
            if isinstance(response, dict):
                resp_obj = response.get('response', {})
                users = resp_obj.get('users', resp_obj.get('items', response.get('items', [])))
            elif isinstance(response, list):
                users = response

            for u in users:
                if u.get('username') == username:
                    return u
            return None
        except Exception as e:
            logger.error(f"Ошибка поиска по username {username}: {e}")
            return None

    # Алиас для обратной совместимости
    async def create_user_with_name(self, telegram_id: int, name: str, expire_at: Optional[str] = None) -> RemnaUser:
        """Алиас для get_or_create_user (обратная совместимость)."""
        return await self.get_or_create_user(telegram_id, name, expire_at)

    async def delete_user(self, user_id: str) -> Dict[str, Any]:
        """Удалить пользователя"""
        return await self.request("DELETE", f"/api/users/{user_id}")

    async def get_user_by_id(self, user_id: str) -> Dict[str, Any]:
        """Получить пользователя по ID"""
        return await self.request("GET", f"/api/users/{user_id}")

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[RemnaUser]:
        """
        Получить пользователя Remna по telegram_id.
        Пробует прямой endpoint /api/users/telegram/{id}.

        Returns:
            RemnaUser если найден, None если не найден
        """
        try:
            # Пробуем прямой endpoint
            response = await self.request("GET", f"/api/users/telegram/{telegram_id}")

            user_data = response.get('response', response) if isinstance(response, dict) else response
            if not isinstance(user_data, dict):
                return None

            uuid = user_data.get('uuid') or user_data.get('id')
            if not uuid:
                return None

            logger.info(f"Найден пользователь Remna: uuid={uuid}, telegram_id={telegram_id}")
            return RemnaUser(
                uuid=str(uuid),
                telegram_id=telegram_id,
                username=user_data.get('username'),
                name=user_data.get('name') or user_data.get('username'),
                raw_data=user_data
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.debug(f"Пользователь telegram_id={telegram_id} не найден в Remna")
                return None
            raise
        except Exception as e:
            logger.error(f"Ошибка get_user_by_telegram_id({telegram_id}): {e}")
            raise

    async def get_user_with_subscription_by_telegram_id(self, telegram_id: int) -> Optional[tuple[RemnaUser, Optional[RemnaSubscription]]]:
        """
        Получить пользователя Remna и его подписку по telegram_id.
        Возвращает кортеж (RemnaUser, RemnaSubscription | None).
        """
        remna_user = await self.get_user_by_telegram_id(telegram_id)
        if not remna_user:
            return None
        
        # Извлекаем информацию о подписке из raw_data пользователя
        subscription = None
        raw_data = remna_user.raw_data
        
        # Проверяем наличие подписки в данных пользователя
        expire_at_raw = raw_data.get('expireAt') or raw_data.get('expires_at') or raw_data.get('valid_until')
        expire_dt = None
        is_active = False
        
        if expire_at_raw:
            try:
                # Парсим дату (может быть ISO строка или timestamp)
                if isinstance(expire_at_raw, str):
                    # Убираем Z и заменяем на +00:00 для fromisoformat
                    expire_str = expire_at_raw.replace('Z', '+00:00')
                    # Если нет таймзоны, добавляем UTC
                    if '+' not in expire_str and '-' not in expire_str[-6:]:
                        expire_str += '+00:00'
                    expire_dt = datetime.fromisoformat(expire_str)
                    # Конвертируем в UTC если нужно
                    if expire_dt.tzinfo:
                        expire_dt = expire_dt.replace(tzinfo=None)
                elif isinstance(expire_at_raw, (int, float)):
                    expire_dt = datetime.fromtimestamp(expire_at_raw)
                elif isinstance(expire_at_raw, datetime):
                    expire_dt = expire_at_raw
                    if expire_dt.tzinfo:
                        expire_dt = expire_dt.replace(tzinfo=None)
                
                # Подписка активна, если expireAt в будущем
                if expire_dt:
                    is_active = expire_dt > datetime.utcnow()
            except Exception as e:
                logger.warning(f"Ошибка парсинга expireAt для пользователя {remna_user.uuid}: {e}")
                expire_dt = None
        
        # Если есть информация о подписке
        if expire_dt or raw_data.get('subscription') or raw_data.get('active'):
            plan = raw_data.get('plan') or raw_data.get('planCode') or raw_data.get('plan_code')
            
            subscription = RemnaSubscription(
                active=is_active,
                expires_at=expire_dt,
                plan=plan,
                raw_data=raw_data.get('subscription', raw_data)
            )
        
        return (remna_user, subscription)

    async def update_user(self, user_id: str, **kwargs) -> Dict[str, Any]:
        """Обновить данные пользователя. kwargs: expire_at/expireAt, telegram_id/telegramId, active_internal_squads/activeInternalSquads и др. (whitelist)."""
        payload = build_user_payload_from_kwargs(kwargs)
        if payload.get("expireAt"):
            logger.debug(f"Remna update_user {user_id}: expireAt={payload['expireAt']}")
        if not payload:
            logger.debug("Remna update_user: пустой payload после фильтрации kwargs")
            return {}
        endpoints = [
            f"/api/user/{user_id}",
            f"/api/users/{user_id}",
            f"/api/users/{user_id}/update",
        ]
        for endpoint in endpoints:
            try:
                return await self.request("PUT", endpoint, json=payload)
            except Exception as e:
                if "404" not in str(e):
                    raise
                continue
        for endpoint in endpoints:
            try:
                return await self.request("PATCH", endpoint, json=payload)
            except Exception as e:
                if "404" not in str(e):
                    raise
                continue
        return await self.request("PUT", f"/api/users/{user_id}", json=payload)

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