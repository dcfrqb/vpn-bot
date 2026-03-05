"""Проверка возможности подключения — только Remnawave."""
from datetime import datetime
from app.services.sync_service import SyncService
from app.logger import logger


async def can_user_connect(telegram_id: int, force_remna: bool = False) -> bool:
    """Проверяет, может ли пользователь подключиться (подписка активна в Remna)."""
    try:
        sync_service = SyncService()
        sync_result = await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name=f"User_{telegram_id}",
            use_fallback=False,
            use_cache=not force_remna,
            force_sync=force_remna,
            force_remna=force_remna,
        )
        if sync_result.subscription_status != "active":
            return False
        if sync_result.expires_at and sync_result.expires_at <= datetime.utcnow():
            return False
        return True
    except Exception as e:
        logger.error(f"Ошибка can_user_connect для {telegram_id}: {e}")
        return False
