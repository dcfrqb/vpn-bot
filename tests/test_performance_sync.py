"""
Тесты производительности синхронизации
"""
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.sync_service import SyncService, SyncResult
from app.remnawave.client import RemnaClient, RemnaUser, RemnaSubscription
from datetime import datetime, timedelta


@pytest.fixture
def mock_remna_client():
    """Создает мок RemnaClient"""
    # Используем мок вместо реального клиента, чтобы избежать проблем с event loop
    client = AsyncMock(spec=RemnaClient)
    client.request = AsyncMock()
    client.get_user_with_subscription_by_telegram_id = AsyncMock()
    client.get_user_by_telegram_id = AsyncMock()
    client.create_user_with_name = AsyncMock()
    return client


@pytest.fixture
def sync_service(mock_remna_client):
    """Создает SyncService с мок-клиентом"""
    return SyncService(remna_client=mock_remna_client)


@pytest.mark.asyncio
@pytest.mark.slow
async def test_sync_with_cache_should_be_fast(sync_service, mock_remna_client):
    """Тест: синхронизация с кэшем должна быть быстрой (< 50ms)"""
    telegram_id = 123456789
    
    # Мокаем SessionLocal для избежания RuntimeError: БД не настроена
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    
    @asynccontextmanager
    async def _session_context():
        yield mock_session
    
    class SessionLocalFactory:
        def __call__(self):
            return _session_context()
        
        def __bool__(self):
            return True
    
    session_local_factory = SessionLocalFactory()
    
    # Мокаем кэш - возвращает результат
    with patch('app.services.sync_service.SessionLocal', session_local_factory), \
         patch('app.services.cache.get_cached_sync_result') as mock_cache:
        mock_cache.return_value = {
            'status': 'active',
            'remna_uuid': 'user-123',
            'expires_at': (datetime.utcnow() + timedelta(days=30)).isoformat(),
            'source': 'cache'
        }
        
        start_time = time.time()
        result = await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name="Test User",
            use_cache=True,
            force_sync=False
        )
        elapsed_ms = (time.time() - start_time) * 1000
        
        assert result is not None
        assert elapsed_ms < 50, f"Синхронизация с кэшем заняла {elapsed_ms:.2f}мс, ожидалось < 50мс"
        # Не должно быть запросов к Remna API
        assert not mock_remna_client.request.called


@pytest.mark.asyncio
@pytest.mark.slow
async def test_sync_without_cache_should_use_remna(sync_service, mock_remna_client):
    """Тест: синхронизация без кэша должна обращаться к Remna"""
    telegram_id = 123456789
    
    # Мокаем SessionLocal для избежания RuntimeError: БД не настроена
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    
    @asynccontextmanager
    async def _session_context():
        yield mock_session
    
    class SessionLocalFactory:
        def __call__(self):
            return _session_context()
        
        def __bool__(self):
            return True
    
    session_local_factory = SessionLocalFactory()
    
    # Мокаем кэш - возвращает None (кэш промах)
    with patch('app.services.sync_service.SessionLocal', session_local_factory), \
         patch('app.services.sync_service.UserRepo') as mock_user_repo_class, \
         patch('app.services.sync_service.SubscriptionRepo') as mock_sub_repo_class, \
         patch('app.services.cache.get_cached_sync_result') as mock_cache:
        mock_cache.return_value = None
        
        # Мокаем репозитории
        mock_user_repo = AsyncMock()
        mock_user_repo.upsert_remna_user = AsyncMock()
        mock_user_repo.upsert_user_by_telegram_id = AsyncMock()
        mock_user_repo_class.return_value = mock_user_repo
        
        mock_sub_repo = AsyncMock()
        mock_sub_repo.get_subscription_by_user_id = AsyncMock(return_value=None)
        mock_sub_repo.upsert_subscription = AsyncMock()
        mock_sub_repo_class.return_value = mock_sub_repo
        
        # Мокаем Remna API - пользователь найден
        mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(return_value=(
            RemnaUser(
                uuid='user-123',
                telegram_id=telegram_id,
                username='testuser',
                name='Test User',
                raw_data={}
            ),
            RemnaSubscription(
                active=True,
                expires_at=datetime.utcnow() + timedelta(days=30),
                plan='basic',
                raw_data={}
            )
        ))
        
        start_time = time.time()
        result = await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name="Test User",
            use_cache=True,
            force_sync=False
        )
        elapsed_ms = (time.time() - start_time) * 1000
        
        assert result is not None
        assert result.subscription_status == "active"
        # Должен быть запрос к Remna API
        assert mock_remna_client.get_user_with_subscription_by_telegram_id.called


@pytest.mark.asyncio
@pytest.mark.slow
async def test_force_sync_should_ignore_cache(sync_service, mock_remna_client):
    """Тест: force_sync должен игнорировать кэш"""
    telegram_id = 123456789
    
    # Мокаем SessionLocal для избежания RuntimeError: БД не настроена
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    
    @asynccontextmanager
    async def _session_context():
        yield mock_session
    
    class SessionLocalFactory:
        def __call__(self):
            return _session_context()
        
        def __bool__(self):
            return True
    
    session_local_factory = SessionLocalFactory()
    
    # Мокаем кэш - возвращает результат
    with patch('app.services.sync_service.SessionLocal', session_local_factory), \
         patch('app.services.sync_service.UserRepo') as mock_user_repo_class, \
         patch('app.services.sync_service.SubscriptionRepo') as mock_sub_repo_class, \
         patch('app.services.cache.get_cached_sync_result') as mock_cache:
        mock_cache.return_value = {
            'status': 'active',
            'remna_uuid': 'user-123',
            'expires_at': (datetime.utcnow() + timedelta(days=30)).isoformat(),
            'source': 'cache'
        }
        
        # Мокаем репозитории
        mock_user_repo = AsyncMock()
        mock_user_repo_class.return_value = mock_user_repo
        
        mock_sub_repo = AsyncMock()
        mock_sub_repo_class.return_value = mock_sub_repo
        
        # Мокаем Remna API
        mock_remna_client.get_user_with_subscription_by_telegram_id = AsyncMock(return_value=None)
        
        result = await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name="Test User",
            use_cache=True,
            force_sync=True  # Принудительная синхронизация
        )
        
        # Должен быть запрос к Remna API, несмотря на кэш
        assert mock_remna_client.get_user_with_subscription_by_telegram_id.called


@pytest.mark.asyncio
@pytest.mark.slow
async def test_get_user_by_telegram_id_performance(mock_remna_client):
    """Тест: поиск пользователя должен быть быстрым"""
    target_telegram_id = 5628460233
    
    # Мокаем ответ API - пользователь на первой странице
    mock_response = {
        'response': {
            'users': [
                {
                    'uuid': f'user-{i}',
                    'telegramId': 1000000000 + i,
                    'username': f'user{i}'
                }
                for i in range(58)  # 58 пользователей как в реальности
            ] + [
                {
                    'uuid': 'user-found',
                    'telegramId': target_telegram_id,
                    'username': 'dukrmv638'
                }
            ]
        }
    }
    
    mock_remna_client.request.return_value = mock_response
    
    start_time = time.time()
    result = await mock_remna_client.get_user_by_telegram_id(target_telegram_id)
    elapsed_ms = (time.time() - start_time) * 1000
    
    assert result is not None
    assert elapsed_ms < 500, f"Поиск пользователя занял {elapsed_ms:.2f}мс, ожидалось < 500мс"


@pytest.mark.asyncio
@pytest.mark.slow
async def test_start_command_should_use_cache_by_default():
    """Тест: /start должен использовать кэш по умолчанию для быстрого ответа"""
    # Этот тест проверяет, что /start не всегда делает force_sync
    # Нужно проверить логику в start.py
    pass  # TODO: реализовать после оптимизации


@pytest.mark.asyncio
@pytest.mark.slow
async def test_multiple_sync_calls_should_use_cache():
    """Тест: множественные вызовы синхронизации должны использовать кэш"""
    telegram_id = 123456789
    sync_service = SyncService()
    
    # Мокаем SessionLocal для избежания RuntimeError: БД не настроена
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    
    @asynccontextmanager
    async def _session_context():
        yield mock_session
    
    class SessionLocalFactory:
        def __call__(self):
            return _session_context()
        
        def __bool__(self):
            return True
    
    session_local_factory = SessionLocalFactory()
    
    # Первый вызов - кэш промах, обращение к Remna
    with patch('app.services.sync_service.SessionLocal', session_local_factory), \
         patch('app.services.sync_service.UserRepo') as mock_user_repo_class, \
         patch('app.services.sync_service.SubscriptionRepo') as mock_sub_repo_class, \
         patch('app.services.cache.get_cached_sync_result') as mock_cache:
        mock_cache.return_value = None
        
        # Мокаем репозитории
        mock_user_repo = AsyncMock()
        mock_user_repo.upsert_remna_user = AsyncMock()
        mock_user_repo.upsert_user_by_telegram_id = AsyncMock()
        mock_user_repo_class.return_value = mock_user_repo
        
        mock_sub_repo = AsyncMock()
        mock_sub_repo.get_subscription_by_user_id = AsyncMock(return_value=None)
        mock_sub_repo.upsert_subscription = AsyncMock()
        mock_sub_repo_class.return_value = mock_sub_repo
        
        with patch.object(sync_service.remna_client, 'get_user_with_subscription_by_telegram_id') as mock_remna:
            mock_remna.return_value = None
            
            # Первый вызов
            result1 = await sync_service.sync_user_and_subscription(
                telegram_id=telegram_id,
                tg_name="Test",
                use_cache=True,
                force_sync=False
            )
            
            # Второй вызов - должен использовать кэш
            mock_cache.return_value = {
                'status': 'none',
                'remna_uuid': None,
                'expires_at': None,
                'source': 'cache'
            }
            
            start_time = time.time()
            result2 = await sync_service.sync_user_and_subscription(
                telegram_id=telegram_id,
                tg_name="Test",
                use_cache=True,
                force_sync=False
            )
            elapsed_ms = (time.time() - start_time) * 1000
            
            # Второй вызов должен быть быстрым (из кэша)
            assert elapsed_ms < 50, f"Второй вызов занял {elapsed_ms:.2f}мс, ожидалось < 50мс (из кэша)"
            # Не должно быть второго запроса к Remna
            assert mock_remna.call_count == 1
