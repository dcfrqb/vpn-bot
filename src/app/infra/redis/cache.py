"""Small JSON cache over Redis (release 3.0 Foundation).

For short-lived derived data (status card, device list). JSON, never pickle.
Fail-open: Redis unavailable -> get returns None, set/invalidate are no-ops.
The 2.x pickle cache (app.services.cache) is still used by 2.x code
(site profile, reconciler, payment locks) and retires in 3.0.1.
"""
import json
from typing import Any, Optional

from app.logger import logger
from app.infra.redis.flags import _client


async def get_json(key: str) -> Optional[Any]:
    try:
        client = _client()
        if client is None:
            return None
        raw = await client.get(key)
    except Exception as e:
        logger.warning(f"redis cache get {key}: unavailable ({e})")
        return None
    if raw is None:
        return None
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


async def set_json(key: str, value: Any, ttl: int) -> bool:
    try:
        client = _client()
        if client is None:
            return False
        await client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=int(ttl))
        return True
    except Exception as e:
        logger.warning(f"redis cache set {key}: unavailable ({e})")
        return False


async def invalidate(*keys: str) -> None:
    if not keys:
        return
    try:
        client = _client()
        if client is not None:
            await client.delete(*keys)
    except Exception as e:
        logger.warning(f"redis cache invalidate {keys}: failed ({e})")
