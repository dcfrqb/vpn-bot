"""Тесты для SyncService"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession

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
    session = AsyncMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock()
    session.close = AsyncMock()
    # Мокаем методы, которые могут вызываться при закрытии
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
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
async def test_sync_existing_user_with_expired_subscription(sync_service, mock_remna_client, mock_session):
    """Тест: пользователь найден в Remna с истекшей подпиской"""
    telegram_id = 12345
    tg_name = "Test User"
    
    # Мок RemnaUser с истекшей подпиской
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
    
    with patch('app.services.sync_service.UserRepo', create=True) as mock_user_repo_class, \
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
            tg_name=tg_name
        )
        
        assert result.subscription_status == "expired"
        # Сравниваем только дату (без времени), так как время может немного отличаться
        assert result.expires_at is not None
        assert result.expires_at.date() == expires_at.date()




@pytest.mark.asyncio
async def test_sync_remna_unavailable_no_fallback(sync_service, mock_remna_client):
    """Тест: Remna недоступна, fallback отключен -> исключение"""
    telegram_id = 12345
    tg_name = "Test User"
    
    # Мок ошибки сети
    from httpx import RequestError
    network_error = RequestError("Connection timeout", request=MagicMock())
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(
        side_effect=network_error
    )
    
    with pytest.raises(RemnaUnavailableError):
        await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name=tg_name,
            use_fallback=False
        )



@pytest.mark.asyncio
async def test_sync_user_not_found_in_remna(sync_service, mock_remna_client, mock_session):
    """Тест: пользователь не найден в Remna -> none"""
    telegram_id = 12345
    tg_name = "Test User"
    
    # Пользователь не найден в Remna
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(return_value=None)
    
    # Мок создания пользователя
    created_remna_user = RemnaUser(
        uuid="remna-uuid-new",
        telegram_id=telegram_id,
        username=f"tg_{telegram_id}",
        name=tg_name,
        raw_data={}
    )
    mock_remna_client.create_user_with_name = AsyncMock(return_value=created_remna_user)
    
    with patch('app.services.sync_service.UserRepo', create=True) as mock_user_repo_class, \
         patch('app.services.sync_service.SubscriptionRepo', create=True) as mock_sub_repo_class:
        
        mock_user_repo = AsyncMock()
        mock_user_repo.upsert_remna_user = AsyncMock()
        mock_user_repo.upsert_user_by_telegram_id = AsyncMock()
        mock_user_repo_class.return_value = mock_user_repo
        
        mock_sub_repo = AsyncMock()
        mock_sub_repo_class.return_value = mock_sub_repo
        
        result = await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name=tg_name
        )
        
        assert result.subscription_status == "none"
        assert result.expires_at is None


@pytest.mark.asyncio
async def test_force_remna_ignores_cache(sync_service, mock_remna_client, mock_session):
    """Тест: force_remna=True -> кэш НЕ используется"""
    telegram_id = 12345
    tg_name = "Test User"
    
    # Мок RemnaUser с активной подпиской
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
    
    # Мокируем кэш - он должен НЕ использоваться при force_remna=True
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
    with patch('app.services.cache.get_cached_sync_result') as mock_get_cache, \
         patch('app.services.sync_service.SessionLocal', session_local_factory, create=True), \
         patch('app.services.sync_service.UserRepo', create=True) as mock_user_repo_class, \
         patch('app.services.sync_service.SubscriptionRepo', create=True) as mock_sub_repo_class:
        
        # Кэш возвращает данные, но они должны быть проигнорированы
        mock_get_cache.return_value = {
            'status': 'expired',  # Устаревшие данные в кэше
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
        
        # Выполняем синхронизацию с force_remna=True
        result = await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name=tg_name,
            force_remna=True
        )
        
        # Проверяем, что результат из Remna (не из кэша)
        assert result.subscription_status == "active"
        assert result.user_remna_uuid == "remna-uuid-123"
        assert result.source == "remna"
        
        # Проверяем, что Remna API был вызван (кэш проигнорирован)
        mock_remna_client.get_user_with_subscription_by_telegram_id.assert_called_once_with(telegram_id)


@pytest.mark.asyncio
async def test_force_remna_ignores_fallback(sync_service, mock_remna_client):
    """Тест: force_remna=True -> fallback НЕ используется"""
    telegram_id = 12345
    tg_name = "Test User"
    
    # Мок ошибки сети (Remna недоступна)
    from httpx import RequestError
    network_error = RequestError("Connection timeout", request=MagicMock())
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(
        side_effect=network_error
    )
    
    # При force_remna=True fallback НЕ должен использоваться, должно быть исключение
    with pytest.raises(RemnaUnavailableError):
        await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name=tg_name,
            force_remna=True,  # Принудительно только Remna
            use_fallback=True  # Даже если fallback включен, он не должен использоваться
        )


@pytest.mark.asyncio
async def test_force_remna_remna_unavailable_raises_error(sync_service, mock_remna_client):
    """Тест: force_remna=True, Remna недоступна -> исключение (не fallback)"""
    telegram_id = 12345
    tg_name = "Test User"
    
    # Мок ошибки сети
    from httpx import RequestError
    network_error = RequestError("Connection timeout", request=MagicMock())
    mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(
        side_effect=network_error
    )
    
    # При force_remna=True должно быть исключение, даже если fallback включен
    with pytest.raises(RemnaUnavailableError) as exc_info:
        await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name=tg_name,
            force_remna=True,
            use_fallback=True  # Fallback включен, но не должен использоваться
        )
    
    assert "Remna API недоступна" in str(exc_info.value)


# Удалены тесты, проверявшие DB-слой SyncService (UserRepo/SubscriptionRepo,
# db_fallback, commit): этого слоя больше нет, SyncService работает только с Remnawave
# (хотфикс 2.1, чистка тестов по 08 §3).
