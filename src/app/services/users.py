"""
Сервис пользователей — данные только из Remnawave.
БД удалена.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.remnawave.client import RemnaClient
from app.services.remna_service import ensure_user_in_remnawave
from app.logger import logger


@dataclass
class SubscriptionInfo:
    """Минимальная информация о подписке (из Remna)"""
    active: bool
    valid_until: Optional[datetime]
    plan_code: str
    plan_name: str
    remna_user_id: Optional[str]
    config_data: dict


async def get_user_active_subscription(telegram_id: int, use_cache: bool = True) -> Optional[SubscriptionInfo]:
    """
    Получает активную подписку пользователя из Remnawave.
    Возвращает SubscriptionInfo или None.
    """
    if use_cache:
        try:
            from app.services.cache import get_cached_sync_result
            cached = await get_cached_sync_result(telegram_id)
            if cached and cached.get("status") == "active":
                return SubscriptionInfo(
                    active=True,
                    valid_until=None,
                    plan_code="",
                    plan_name="",
                    remna_user_id=cached.get("remna_uuid"),
                    config_data={},
                )
            if cached and cached.get("status") in ("expired", "none"):
                return None
        except Exception:
            pass

    try:
        client = RemnaClient()
        result = await client.get_user_with_subscription_by_telegram_id(telegram_id)
        await client.close()

        if not result:
            return None

        remna_user, remna_subscription = result
        if not remna_subscription or not remna_subscription.active:
            return None

        return SubscriptionInfo(
            active=True,
            valid_until=remna_subscription.expires_at,
            plan_code=remna_subscription.plan or "",
            plan_name=remna_subscription.plan or "",
            remna_user_id=remna_user.uuid,
            config_data={},
        )
    except Exception as e:
        logger.debug(f"Ошибка получения подписки для {telegram_id}: {e}")
        return None


async def update_user_activity(user_id: int) -> None:
    """Заглушка: обновление активности пользователя."""
    pass


async def get_or_create_telegram_user(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    language_code: Optional[str] = None,
    create_trial: bool = True,
):
    """
    Заглушка: создаёт/проверяет пользователя в Remnawave.
    Возвращает объект с telegram_id, username, first_name для совместимости.
    """
    name = first_name or username or f"User_{telegram_id}"
    remna_user_id = await ensure_user_in_remnawave(telegram_id, username=username, name=name)
    return type("User", (), {
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "language_code": language_code,
        "remna_user_id": remna_user_id,
        "created_at": datetime.utcnow(),
    })()
