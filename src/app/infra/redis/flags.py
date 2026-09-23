"""Общие Redis-примитивы «поставить флаг один раз» (фикс-раунд 1, ревью A-M4).

С 3.0 живет в app.infra.redis.flags (старый путь app.services.redis_flags
оставлен шимом). Добавлены счетчик incr_counter и маркер «обработать один раз»
(acquire_marker / mark_marker_done / release_marker), на котором стоит дедуп
вебхуков ЮKassa (services/payments/webhook_dedup) и будет стоять дедуп
вебхуков панели.

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
    from app.infra.redis import get_client

    return get_client()


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


async def incr_counter(key: str, ttl: Optional[int] = None) -> Optional[int]:
    """INCR key (+EXPIRE ttl на первом инкременте). None — Redis недоступен."""
    try:
        client = _client()
        if client is None:
            return None
        value = int(await client.incr(key))
        if ttl and value == 1:
            await client.expire(key, ttl)
        return value
    except Exception as e:
        logger.warning(f"redis incr {key}: unavailable ({e})")
        return None


# ---------------------------------------------------------------------------
# Маркер «обработать один раз» (processing -> done), общий для вебхуков.
# ---------------------------------------------------------------------------

MARKER_ACQUIRED = "acquired"
MARKER_DUPLICATE = "duplicate"
MARKER_IN_PROGRESS = "in_progress"
MARKER_UNAVAILABLE = "unavailable"


async def acquire_marker(key: str, trace_id: str, processing_ttl: int) -> str:
    """Пытается взять маркер key = processing:<trace_id>.

    MARKER_ACQUIRED    — взяли, обрабатываем;
    MARKER_DUPLICATE   — уже done (дубль, ничего не делаем);
    MARKER_IN_PROGRESS — другая доставка обрабатывает сейчас (или состояние
                         неизвестно) -> просим повтор позже;
    MARKER_UNAVAILABLE — Redis недоступен, решает вызывающий.

    Значения маркера всегда непустые, поэтому «нет значения» никогда не
    значит «done»: если маркер исчез между SET NX и GET (первая доставка упала
    и сняла его), пробуем взять еще раз, иначе IN_PROGRESS (ревью N4).
    """
    got = await set_once(key, f"processing:{trace_id}", ttl=processing_ttl)
    if got is None:
        return MARKER_UNAVAILABLE
    if got:
        return MARKER_ACQUIRED
    current = await get_value(key)
    if current is None:
        got = await set_once(key, f"processing:{trace_id}", ttl=processing_ttl)
        if got:
            return MARKER_ACQUIRED
        current = await get_value(key)
        if current is None:
            logger.info(f"[{trace_id}] marker {key}: state unknown -> retry later")
            return MARKER_IN_PROGRESS
    if current.startswith("processing"):
        logger.info(f"[{trace_id}] marker {key}: first delivery still in progress -> retry later")
        return MARKER_IN_PROGRESS
    return MARKER_DUPLICATE


async def mark_marker_done(key: str, trace_id: str, ttl: int) -> None:
    await set_value(key, f"done:{trace_id}", ttl=ttl)


async def release_marker(key: str) -> None:
    await delete_key(key)
