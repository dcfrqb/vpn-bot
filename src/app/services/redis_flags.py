"""Общие Redis-примитивы «поставить флаг один раз» (фикс-раунд 1, ревью A-M4).

Раньше SET NX с fail-open был написан трижды (лок промо, дедуп вебхука,
дедуп алерта). Теперь одно место, одна политика:

- Redis недоступен или упал -> результат None, вызывающий решает сам
  (обычно fail-open: работаем без флага, страхует БД).
- Клиент берется через app.services.cache.get_redis_client при каждом вызове
  (тесты подменяют именно его).
"""
from typing import Optional

from app.logger import logger


def _client():
    from app.services.cache import get_redis_client

    return get_redis_client()


async def set_once(key: str, value: str = "1", ttl: Optional[int] = None) -> Optional[bool]:
    """SET key value NX [EX ttl].

    True — флаг поставлен нами; False — уже стоял; None — Redis недоступен.
    """
    try:
        client = _client()
        if client is None:
            return None
        return bool(await client.set(key, value, ex=ttl, nx=True))
    except Exception as e:
        logger.warning(f"redis set_once {key}: unavailable ({e})")
        return None


async def get_value(key: str) -> Optional[str]:
    """Значение ключа (str) или None (нет ключа / Redis недоступен)."""
    try:
        client = _client()
        if client is None:
            return None
        raw = await client.get(key)
    except Exception as e:
        logger.warning(f"redis get {key}: unavailable ({e})")
        return None
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)


async def set_value(key: str, value: str, ttl: Optional[int] = None) -> bool:
    """Безусловный SET. False при недоступности Redis."""
    try:
        client = _client()
        if client is None:
            return False
        await client.set(key, value, ex=ttl)
        return True
    except Exception as e:
        logger.warning(f"redis set {key}: unavailable ({e})")
        return False


async def delete_key(key: str) -> None:
    try:
        client = _client()
        if client is not None:
            await client.delete(key)
    except Exception as e:
        logger.warning(f"redis delete {key}: failed ({e})")


async def compare_and_delete(key: str, token: str) -> None:
    """Удаляет ключ, только если в нем все еще token (снятие своего лока)."""
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then\n"
        "    return redis.call('del', KEYS[1])\n"
        "end\n"
        "return 0\n"
    )
    try:
        client = _client()
        if client is not None:
            await client.eval(script, 1, key, token)
    except Exception as e:
        logger.debug(f"redis compare_and_delete {key}: failed (TTL cleans up): {e}")
