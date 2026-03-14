from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable, Union
from app.config import settings
from app.logger import logger

BLOCKLIST_REDIS_KEY = "vpnbot:blocked_users"

# Runtime blocklist: populated from config + Redis on startup.
# In-memory for fast middleware checks (no async in hot path).
_runtime_blocked: set[int] = set(settings.BLOCKED_TELEGRAM_IDS or [])


async def load_blocklist_from_redis() -> None:
    """Load persisted blocked IDs from Redis into _runtime_blocked. Call once at startup."""
    try:
        from app.services.cache import get_redis_client
        client = get_redis_client()
        if not client:
            return
        members = await client.smembers(BLOCKLIST_REDIS_KEY)
        for m in members:
            try:
                _runtime_blocked.add(int(m))
            except (ValueError, TypeError):
                pass
        if members:
            logger.info(f"blocklist: loaded {len(members)} blocked users from Redis")
    except Exception as e:
        logger.warning(f"blocklist: could not load from Redis: {e}")


async def block_user(user_id: int) -> None:
    _runtime_blocked.add(user_id)
    try:
        from app.services.cache import get_redis_client
        client = get_redis_client()
        if client:
            await client.sadd(BLOCKLIST_REDIS_KEY, str(user_id))
    except Exception as e:
        logger.warning(f"blocklist: Redis persist failed for block {user_id}: {e}")
    logger.info(f"blocklist: user {user_id} blocked")


async def unblock_user(user_id: int) -> None:
    _runtime_blocked.discard(user_id)
    try:
        from app.services.cache import get_redis_client
        client = get_redis_client()
        if client:
            await client.srem(BLOCKLIST_REDIS_KEY, str(user_id))
    except Exception as e:
        logger.warning(f"blocklist: Redis persist failed for unblock {user_id}: {e}")
    logger.info(f"blocklist: user {user_id} unblocked")


def is_blocked(user_id: int) -> bool:
    return user_id in _runtime_blocked


class BlocklistMiddleware(BaseMiddleware):
    """Stops blocked users before any handler runs. Admins always bypass."""

    async def __call__(
        self,
        handler: Callable[[Union[Message, CallbackQuery], Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id if event.from_user else None
        if user_id and user_id in _runtime_blocked and user_id not in (settings.ADMINS or []):
            logger.info(f"blocklist: blocked user {user_id} — request dropped")
            if isinstance(event, Message):
                await event.answer("⛔ Доступ ограничен.")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ ограничен.", show_alert=True)
            return
        return await handler(event, data)
