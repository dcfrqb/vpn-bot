"""
Вспомогательные функции для работы с экранами
"""
from typing import Optional
from datetime import datetime
from app.services.users import get_user_active_subscription
from app.services.sync_service import SyncService, RemnaUnavailableError
from app.services.cache import get_cached_sync_result
from app.routers.subscription_view import SubscriptionViewModel, create_subscription_view_model
from app.ui.screens.main_menu import MainMenuScreen
from app.ui.screens.profile import ProfileScreen
from app.ui.screens import ScreenID
from app.logger import logger


async def get_main_menu_viewmodel(
    telegram_id: int,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    username: Optional[str] = None,
    use_cache: bool = True,
    force_sync: bool = False
) -> 'MainMenuViewModel':
    """
    Получает ViewModel для главного меню
    
    Args:
        telegram_id: ID пользователя Telegram
        first_name: Имя пользователя
        last_name: Фамилия пользователя
        username: Username пользователя
        use_cache: Использовать кэш
        force_sync: Принудительная синхронизация
        
    Returns:
        MainMenuViewModel
    """
    # Получаем данные синхронизации
    sync_result = None
    
    if use_cache and not force_sync:
        cached = await get_cached_sync_result(telegram_id)
        if cached:
            expires_at = None
            if cached.get('expires_at'):
                try:
                    if isinstance(cached['expires_at'], str):
                        expires_at = datetime.fromisoformat(cached['expires_at'])
                    else:
                        expires_at = cached['expires_at']
                except Exception:
                    pass
            
            from app.services.sync_service import SyncResult
            sync_result = SyncResult(
                is_new_user_created=False,
                user_remna_uuid=cached.get('remna_uuid'),
                subscription_status=cached.get('status', 'none'),
                expires_at=expires_at,
                source=cached.get('source', 'cache')
            )
    
    # Если кэша нет или нужна принудительная синхронизация
    if not sync_result:
        sync_service = SyncService()
        try:
            sync_result = await sync_service.sync_user_and_subscription(
                telegram_id=telegram_id,
                tg_name=f"{first_name or ''} {last_name or ''}".strip() or username or f"User_{telegram_id}",
                use_fallback=not force_sync,
                use_cache=use_cache,
                force_sync=force_sync
            )
        except RemnaUnavailableError:
            # Remna недоступна, используем данные из БД
            logger.warning(f"Remna недоступна для {telegram_id}, используем данные из БД")
            subscription = await get_user_active_subscription(telegram_id, use_cache=True)
            subscription_status = "none"
            expires_at = None
            if subscription and subscription.valid_until:
                if subscription.valid_until > datetime.utcnow():
                    subscription_status = "active"
                    expires_at = subscription.valid_until
                else:
                    subscription_status = "expired"
                    expires_at = subscription.valid_until
            
            from app.services.sync_service import SyncResult
            sync_result = SyncResult(
                is_new_user_created=False,
                user_remna_uuid=None,
                subscription_status=subscription_status,
                expires_at=expires_at,
                source="db_fallback"
            )
        except Exception as e:
            logger.error(f"Ошибка синхронизации для {telegram_id}: {e}")
            # Используем данные из БД как последний fallback
            subscription = await get_user_active_subscription(telegram_id, use_cache=True)
            subscription_status = "none"
            expires_at = None
            if subscription and subscription.valid_until:
                if subscription.valid_until > datetime.utcnow():
                    subscription_status = "active"
                    expires_at = subscription.valid_until
                else:
                    subscription_status = "expired"
                    expires_at = subscription.valid_until
            
            from app.services.sync_service import SyncResult
            sync_result = SyncResult(
                is_new_user_created=False,
                user_remna_uuid=None,
                subscription_status=subscription_status,
                expires_at=expires_at,
                source="db_fallback"
            )
    
    # Создаем SubscriptionViewModel
    subscription_vm = None
    if sync_result:
        subscription_vm = create_subscription_view_model(
            subscription_status=sync_result.subscription_status,
            expires_at=sync_result.expires_at,
            days_left=None,
            source=sync_result.source
        )
    else:
        # Fallback: определяем из subscription
        subscription = await get_user_active_subscription(telegram_id, use_cache=True)
        if subscription and subscription.valid_until:
            if subscription.valid_until > datetime.utcnow():
                subscription_status = "active"
                expires_at = subscription.valid_until
            else:
                subscription_status = "expired"
                expires_at = subscription.valid_until
        else:
            subscription_status = "none"
            expires_at = None
        
        subscription_vm = create_subscription_view_model(
            subscription_status=subscription_status,
            expires_at=expires_at,
            days_left=None,
            source="db_fallback"
        )
    
    # Создаем MainMenuViewModel
    screen = MainMenuScreen()
    return await screen.create_viewmodel(
        user_id=telegram_id,
        user_first_name=first_name,
        user_last_name=last_name,
        user_username=username,
        subscription_view_model=subscription_vm
    )


async def get_connect_viewmodel(
    telegram_id: int,
    subscription_url: Optional[str] = None,
    status: str = "loading",
    error_message: Optional[str] = None
) -> 'ConnectViewModel':
    """
    Получает ViewModel для экрана подключения
    
    Args:
        telegram_id: ID пользователя Telegram
        subscription_url: URL подписки (если есть)
        status: Статус ("loading", "success", "error", "no_subscription")
        error_message: Сообщение об ошибке (если есть)
        
    Returns:
        ConnectViewModel
    """
    from app.services.connection import can_user_connect
    from app.ui.screens.connect import ConnectScreen
    
    # Проверяем, может ли пользователь подключиться
    has_subscription = await can_user_connect(telegram_id)
    
    # Если статус не указан явно, определяем его
    if status == "loading":
        if not has_subscription:
            status = "no_subscription"
        elif subscription_url:
            status = "success"
    
    screen = ConnectScreen()
    return await screen.create_viewmodel(
        has_subscription=has_subscription,
        subscription_url=subscription_url,
        status=status,
        error_message=error_message
    )


async def get_profile_viewmodel(telegram_id: int) -> 'ProfileViewModel':
    """
    Получает ViewModel для экрана профиля
    
    Args:
        telegram_id: ID пользователя Telegram
        
    Returns:
        ProfileViewModel
    """
    from app.services.users import get_or_create_telegram_user, get_user_active_subscription
    from app.services.stats import get_user_payment_stats
    
    # Получаем пользователя
    user = await get_or_create_telegram_user(
        telegram_id=telegram_id,
        username=None,  # Не обновляем, только получаем
        first_name=None,
        last_name=None,
        language_code=None,
        create_trial=False
    )
    
    # Получаем подписку
    subscription = await get_user_active_subscription(telegram_id, use_cache=True)
    
    # Получаем статистику платежей через сервис (НЕ прямой доступ к БД)
    payment_stats = await get_user_payment_stats(telegram_id)
    total_payments = payment_stats["total_payments"]
    total_spent = payment_stats["total_spent"]
    
    # Определяем данные подписки
    subscription_plan = None
    subscription_valid_until = None
    subscription_days_left = None
    
    if subscription:
        subscription_plan = subscription.plan_name or (subscription.plan_code.upper() if subscription.plan_code else None)
        if subscription.valid_until:
            subscription_valid_until = subscription.valid_until
            if subscription.valid_until > datetime.utcnow():
                subscription_days_left = (subscription.valid_until - datetime.utcnow()).days
    
    # Создаем ViewModel
    screen = ProfileScreen()
    return await screen.create_viewmodel(
        user_id=telegram_id,
        username=user.username if hasattr(user, 'username') else None,
        created_at=user.created_at if hasattr(user, 'created_at') and user.created_at else None,
        subscription_plan=subscription_plan,
        subscription_valid_until=subscription_valid_until,
        subscription_days_left=subscription_days_left,
        total_payments=total_payments,
        total_spent=total_spent
    )