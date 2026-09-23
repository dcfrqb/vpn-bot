"""Пользовательский Redis-лок для промо и ручных выдач (хотфикс 2.1).

aiogram обрабатывает апдейты параллельно: 5 быстрых /trial проходили проверку
«уже использовал?» одновременно и продлевали доступ 5 раз. Лок SET NX с TTL
сериализует такие действия по юзеру. Это первая линия; вторая — запись факта
использования промокода ДО выдачи (уникальный external_id в payments).

Если Redis недоступен, лок не блокирует (fail-open): от двойной выдачи в этом
случае защищает уникальная запись в БД.
"""
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.logger import logger

DEFAULT_LOCK_TTL_SECONDS = 120

# Снимаем лок только если он все еще наш (TTL мог истечь и лок взял другой).
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


@asynccontextmanager
async def user_action_lock(
    scope: str, user_id: int, ttl: int = DEFAULT_LOCK_TTL_SECONDS
) -> AsyncIterator[bool]:
    """async with user_action_lock("promo", uid) as acquired: ...

    acquired=False — такое же действие этого юзера уже выполняется.
    """
    key = f"lock:{scope}:{int(user_id)}"
    token = uuid.uuid4().hex
    client = None
    acquired = True
    try:
        from app.services.cache import get_redis_client
        client = get_redis_client()
        if client is not None:
            acquired = bool(await client.set(key, token, ex=ttl, nx=True))
    except Exception as e:
        logger.warning(f"user_action_lock {key}: redis unavailable, continuing without lock: {e}")
        client = None
        acquired = True

    try:
        yield acquired
    finally:
        if client is not None and acquired:
            try:
                await client.eval(_RELEASE_LUA, 1, key, token)
            except Exception as e:
                logger.debug(f"user_action_lock {key}: release failed (TTL cleans up): {e}")
