"""
Anti-spam для заявок на оплату.
Redis с in-memory fallback.
"""
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from app.services.cache import get_redis_client
from app.logger import logger

# In-memory fallback (теряется при рестарте, но лучше чем ничего)
_MEMORY_CACHE: Dict[int, Tuple[str, float]] = {}

# Настройки
ANTISPAM_TTL_SECONDS = 120
ANTISPAM_PREFIX = "payreq_antispam:"


async def check_antispam(tg_id: int) -> Tuple[bool, Optional[str]]:
    """
    Проверяет, может ли пользователь создать новую заявку.

    Returns:
        (can_create, existing_req_id):
        - (True, None) — можно создать
        - (False, req_id) — недавно уже создал, вот req_id
    """
    client = get_redis_client()

    if client:
        try:
            key = f"{ANTISPAM_PREFIX}{tg_id}"
            data = await client.get(key)
            if data:
                req_id = data.decode() if isinstance(data, bytes) else str(data)
                return (False, req_id)
            return (True, None)
        except Exception as e:
            logger.debug(f"Redis antispam check failed: {e}")
            # Fallback to memory

    # In-memory fallback
    now = datetime.now(timezone.utc).timestamp()
    if tg_id in _MEMORY_CACHE:
        req_id, ts = _MEMORY_CACHE[tg_id]
        if now - ts < ANTISPAM_TTL_SECONDS:
            return (False, req_id)
        del _MEMORY_CACHE[tg_id]

    return (True, None)


async def record_antispam(tg_id: int, req_id: str) -> None:
    """
    Записывает, что пользователь создал заявку.
    """
    client = get_redis_client()

    if client:
        try:
            key = f"{ANTISPAM_PREFIX}{tg_id}"
            await client.setex(key, ANTISPAM_TTL_SECONDS, req_id)
            return
        except Exception as e:
            logger.debug(f"Redis antispam record failed: {e}")
            # Fallback to memory

    # In-memory fallback
    _MEMORY_CACHE[tg_id] = (req_id, datetime.now(timezone.utc).timestamp())


async def clear_antispam(tg_id: int) -> None:
    """
    Очищает блокировку (для тестов или ручного сброса).
    """
    client = get_redis_client()

    if client:
        try:
            key = f"{ANTISPAM_PREFIX}{tg_id}"
            await client.delete(key)
        except Exception as e:
            logger.debug(f"Redis antispam clear failed: {e}")

    if tg_id in _MEMORY_CACHE:
        del _MEMORY_CACHE[tg_id]


def cleanup_memory_cache() -> int:
    """
    Очищает устаревшие записи из in-memory кэша.
    Возвращает количество удалённых.
    """
    now = datetime.now(timezone.utc).timestamp()
    expired = [
        tg_id for tg_id, (_, ts) in _MEMORY_CACHE.items()
        if now - ts >= ANTISPAM_TTL_SECONDS
    ]
    for tg_id in expired:
        del _MEMORY_CACHE[tg_id]
    return len(expired)
