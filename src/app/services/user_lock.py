"""Пользовательский Redis-лок для промо и ручных выдач (хотфикс 2.1).

aiogram обрабатывает апдейты параллельно: 5 быстрых /trial проходили проверку
«уже использовал?» одновременно и продлевали доступ 5 раз. Лок SET NX с TTL
сериализует такие действия по юзеру. Это первая линия; вторая — запись факта
использования промокода ДО выдачи (уникальный external_id в payments).

Если Redis недоступен, лок не блокирует (fail-open): от двойной выдачи в этом
случае защищает уникальная запись в БД. Примитивы — services/redis_flags.
"""
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.logger import logger
from app.services.redis_flags import compare_and_delete, set_once

DEFAULT_LOCK_TTL_SECONDS = 120


@asynccontextmanager
async def user_action_lock(
    scope: str, user_id: int, ttl: int = DEFAULT_LOCK_TTL_SECONDS
) -> AsyncIterator[bool]:
    """async with user_action_lock("promo", uid) as acquired: ...

    acquired=False — такое же действие этого юзера уже выполняется.
    """
    key = f"lock:{scope}:{int(user_id)}"
    token = uuid.uuid4().hex
    got = await set_once(key, token, ttl=ttl)
    if got is None:
        logger.warning(f"user_action_lock {key}: redis unavailable, continuing without lock")
    acquired = got is not False
    try:
        yield acquired
    finally:
        if got is True:
            await compare_and_delete(key, token)
