"""
Сервис синхронизации с Remnawave.
Remnawave — единственный источник правды. БД удалена.
"""
import time
from datetime import datetime
from typing import Optional
from dataclasses import dataclass

from app.remnawave.client import RemnaClient, RemnaUser, RemnaSubscription
from app.services.remna_service import ensure_user_in_remnawave
from app.logger import logger


@dataclass
class SyncResult:
    """Результат синхронизации"""
    is_new_user_created: bool
    user_remna_uuid: Optional[str]
    subscription_status: str  # "active", "expired", "none"
    expires_at: Optional[datetime]
    source: str = "remna"


class RemnaUnavailableError(Exception):
    """Ошибка недоступности Remna API"""
    pass


class SyncService:
    """Сервис синхронизации — только Remnawave, без БД."""

    def __init__(self, remna_client: Optional[RemnaClient] = None):
        self.remna_client = remna_client or RemnaClient()

    async def sync_user_and_subscription(
        self,
        telegram_id: int,
        tg_name: str = "",
        use_fallback: bool = False,
        use_cache: bool = True,
        force_sync: bool = False,
        force_remna: bool = False,
        tg_username: Optional[str] = None,
        tg_first_name: Optional[str] = None,
        tg_last_name: Optional[str] = None,
    ) -> SyncResult:
        """
        Синхронизирует пользователя с Remnawave.
        Возвращает статус подписки из Remna.
        """
        start_time = time.time()

        # Проверяем кэш (Redis), если не force
        if use_cache and not force_sync and not force_remna:
            try:
                from app.services.cache import get_cached_sync_result
                cached = await get_cached_sync_result(telegram_id)
                if cached:
                    expires_at = None
                    if cached.get("expires_at"):
                        try:
                            ex = cached["expires_at"]
                            expires_at = datetime.fromisoformat(ex) if isinstance(ex, str) else ex
                        except Exception:
                            pass
                    return SyncResult(
                        is_new_user_created=False,
                        user_remna_uuid=cached.get("remna_uuid"),
                        subscription_status=cached.get("status", "none"),
                        expires_at=expires_at,
                        source="cache",
                    )
            except Exception as e:
                logger.debug(f"Кэш недоступен: {e}")

        try:
            result = await self.remna_client.get_user_with_subscription_by_telegram_id(telegram_id)
        except Exception as e:
            logger.warning(f"Remna API недоступна для {telegram_id}: {e}")
            if force_remna:
                raise RemnaUnavailableError(f"Remna API недоступна: {e}")
            if use_fallback:
                return SyncResult(
                    is_new_user_created=False,
                    user_remna_uuid=None,
                    subscription_status="none",
                    expires_at=None,
                    source="error_fallback",
                )
            raise RemnaUnavailableError(f"Remna API недоступна: {e}")

        if not result:
            remna_user_id = await ensure_user_in_remnawave(
                telegram_id,
                username=tg_username,
                tg_first_name=tg_first_name,
                tg_last_name=tg_last_name,
            )
            if remna_user_id:
                try:
                    await self._save_sync_result_to_cache(
                        telegram_id,
                        SyncResult(
                            is_new_user_created=True,
                            user_remna_uuid=remna_user_id,
                            subscription_status="none",
                            expires_at=None,
                            source="remna",
                        ),
                    )
                except Exception:
                    pass
                return SyncResult(
                    is_new_user_created=True,
                    user_remna_uuid=remna_user_id,
                    subscription_status="none",
                    expires_at=None,
                    source="remna",
                )
            return SyncResult(
                is_new_user_created=False,
                user_remna_uuid=None,
                subscription_status="none",
                expires_at=None,
                source="remna",
            )

        remna_user, remna_subscription = result
        subscription_status = "active" if (remna_subscription and remna_subscription.active) else "expired" if remna_subscription else "none"
        expires_at = remna_subscription.expires_at if remna_subscription else None

        sync_result = SyncResult(
            is_new_user_created=False,
            user_remna_uuid=remna_user.uuid,
            subscription_status=subscription_status,
            expires_at=expires_at,
            source="remna",
        )

        if use_cache:
            try:
                await self._save_sync_result_to_cache(telegram_id, sync_result)
            except Exception:
                pass

        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Синхронизация для {telegram_id}: status={subscription_status}, "
            f"remna_uuid={remna_user.uuid}, {elapsed_ms:.2f}мс"
        )
        return sync_result

    async def _save_sync_result_to_cache(self, telegram_id: int, result: SyncResult, ttl: int = 300) -> None:
        try:
            from app.services.cache import set_cached_sync_result
            cached = {
                "remna_uuid": result.user_remna_uuid,
                "status": result.subscription_status,
                "expires_at": result.expires_at.isoformat() if result.expires_at else None,
                "updated_at": datetime.utcnow().isoformat(),
                "source": result.source,
            }
            await set_cached_sync_result(telegram_id, cached, ttl=ttl)
        except Exception:
            pass
