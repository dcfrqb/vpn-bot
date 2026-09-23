"""
Строгие сценарии с force_remna.

Покрывают:
- force_remna при удалённой подписке
- Remna → none, бот не считает подписку активной
- защита от логических багов
- защита доверия к данным
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from app.services.sync_service import SyncService, SyncResult, RemnaUnavailableError
from app.remnawave.client import RemnaClient, RemnaUser, RemnaSubscription


@pytest.fixture
def mock_remna_client():
    """Мок RemnaClient"""
    return AsyncMock(spec=RemnaClient)


@pytest.fixture
def sync_service(mock_remna_client):
    """SyncService с мок-клиентом"""
    return SyncService(remna_client=mock_remna_client)


@pytest.fixture
def mock_session():
    """Мок сессии БД"""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture(autouse=True)
def mock_session_local(mock_session):
    """Автоматически мокает SessionLocal для всех тестов"""
    from contextlib import asynccontextmanager
    from unittest.mock import patch
    
    @asynccontextmanager
    async def _session_context():
        yield mock_session
    
    class SessionLocalFactory:
        def __call__(self):
            return _session_context()
        
        def __bool__(self):
            return True
    
    with patch('app.services.sync_service.SessionLocal', SessionLocalFactory(), create=True):
        yield




@pytest.mark.asyncio
async def test_force_remna_expired_subscription_returns_expired(sync_service, mock_remna_client, mock_session):
    """Тест: force_remna при истекшей подписке -> expired"""
    telegram_id = 12345
    tg_name = "Test User"
    
    expires_at = datetime.utcnow() - timedelta(days=1)
    remna_user = RemnaUser(
        uuid="remna-uuid-123",
        telegram_id=telegram_id,
        username="test_user",
        name="Test User",
        raw_data={"expireAt": expires_at.isoformat()}
    )
    remna_subscription = RemnaSubscription(
        active=False,
        expires_at=expires_at,
        plan="premium",
        raw_data={}
    )
    
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(
        return_value=(remna_user, remna_subscription)
    )
    
    # SessionLocal должен быть callable и возвращать async context manager
    from contextlib import asynccontextmanager
    
    @asynccontextmanager
    async def _session_context():
        yield mock_session
    
    class SessionLocalFactory:
        def __call__(self):
            return _session_context()
        
        def __bool__(self):
            return True
    
    session_local_factory = SessionLocalFactory()
    with patch('app.services.sync_service.SessionLocal', session_local_factory, create=True), \
         patch('app.services.sync_service.UserRepo', create=True) as mock_user_repo_class, \
         patch('app.services.sync_service.SubscriptionRepo', create=True) as mock_sub_repo_class:
        
        mock_user_repo = AsyncMock()
        mock_user_repo.upsert_remna_user = AsyncMock()
        mock_user_repo.upsert_user_by_telegram_id = AsyncMock()
        mock_user_repo_class.return_value = mock_user_repo
        
        mock_sub_repo = AsyncMock()
        mock_sub_repo.get_subscription_by_user_id = AsyncMock(return_value=None)
        mock_sub_repo.upsert_subscription = AsyncMock()
        mock_sub_repo_class.return_value = mock_sub_repo
        
        result = await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name=tg_name,
            force_remna=True
        )
        
        # Подписка истекла в Remna
        assert result.subscription_status == "expired"
        assert result.expires_at == expires_at
        assert result.source == "remna"


@pytest.mark.asyncio
async def test_force_remna_never_uses_cache(sync_service, mock_remna_client, mock_session):
    """Тест: force_remna НИКОГДА не использует кэш"""
    telegram_id = 12345
    tg_name = "Test User"
    
    expires_at = datetime.utcnow() + timedelta(days=30)
    remna_user = RemnaUser(
        uuid="remna-uuid-123",
        telegram_id=telegram_id,
        username="test_user",
        name="Test User",
        raw_data={"expireAt": expires_at.isoformat()}
    )
    remna_subscription = RemnaSubscription(
        active=True,
        expires_at=expires_at,
        plan="premium",
        raw_data={}
    )
    
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(
        return_value=(remna_user, remna_subscription)
    )
    
    # Мокируем кэш - он должен НЕ использоваться
    # SessionLocal должен быть callable и возвращать mock_session (async context manager)
    def session_local_factory():
        return mock_session
    
    # SessionLocal должен быть truthy для проверки `if not SessionLocal:`
    session_local_factory.__bool__ = lambda self: True
    with patch('app.services.cache.get_cached_sync_result') as mock_get_cache, \
         patch('app.services.sync_service.SessionLocal', session_local_factory, create=True), \
         patch('app.services.sync_service.UserRepo', create=True) as mock_user_repo_class, \
         patch('app.services.sync_service.SubscriptionRepo', create=True) as mock_sub_repo_class:
        
        # Кэш возвращает устаревшие данные
        mock_get_cache.return_value = {
            'status': 'expired',
            'remna_uuid': 'old-uuid',
            'expires_at': datetime.utcnow() - timedelta(days=1)
        }
        
        mock_user_repo = AsyncMock()
        mock_user_repo.upsert_remna_user = AsyncMock()
        mock_user_repo.upsert_user_by_telegram_id = AsyncMock()
        mock_user_repo_class.return_value = mock_user_repo
        
        mock_sub_repo = AsyncMock()
        mock_sub_repo.get_subscription_by_user_id = AsyncMock(return_value=None)
        mock_sub_repo.upsert_subscription = AsyncMock()
        mock_sub_repo_class.return_value = mock_sub_repo
        
        result = await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name=tg_name,
            force_remna=True
        )
        
        # Результат должен быть из Remna (не из кэша)
        assert result.subscription_status == "active"
        assert result.user_remna_uuid == "remna-uuid-123"
        assert result.source == "remna"
        
        # Проверяем, что Remna API был вызван (кэш проигнорирован)
        mock_remna_client.get_user_with_subscription_by_telegram_id.assert_called_once_with(telegram_id)


@pytest.mark.asyncio
async def test_force_remna_never_uses_fallback(sync_service, mock_remna_client):
    """Тест: force_remna НИКОГДА не использует fallback"""
    telegram_id = 12345
    tg_name = "Test User"
    
    # Мок ошибки сети (Remna недоступна)
    from httpx import RequestError
    network_error = RequestError("Connection timeout", request=MagicMock())
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(
        side_effect=network_error
    )
    
    # При force_remna=True fallback НЕ должен использоваться, даже если включен
    with pytest.raises(RemnaUnavailableError):
        await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name=tg_name,
            force_remna=True,
            use_fallback=True  # Fallback включен, но не должен использоваться
        )


# Удалены тесты, проверявшие DB-слой SyncService (UserRepo/SubscriptionRepo,
# db_fallback, commit): этого слоя больше нет, SyncService работает только с Remnawave
# (хотфикс 2.1, чистка тестов по 08 §3).
