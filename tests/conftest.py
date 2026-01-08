"""Конфигурация pytest для тестов"""
import pytest
import asyncio
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.models import Base
from app.db.session import SessionLocal
from app.config import settings


# Тестовая база данных (in-memory SQLite для unit тестов)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="function")
async def test_db_session():
    """Создает тестовую сессию БД для unit тестов"""
    # Создаем in-memory SQLite базу
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Создаем все таблицы
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Создаем сессию
    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with async_session_maker() as session:
        yield session
        await session.rollback()  # Откатываем изменения
    
    # Очищаем после теста
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest.fixture(scope="function")
async def test_db_with_postgres(monkeypatch):
    """Создает тестовую сессию БД с PostgreSQL для интеграционных тестов"""
    # Определяем URL тестовой БД
    # В контейнере (Docker) используем db_test:5432
    # На хосте используем localhost:5433
    test_db_url = os.getenv(
        "TEST_DATABASE_URL",
        None
    )
    
    if not test_db_url:
        # Проверяем, запущены ли мы в Docker контейнере
        # Если файл /.dockerenv существует, мы в контейнере
        if os.path.exists("/.dockerenv"):
            test_db_url = "postgresql+asyncpg://crs_user_test:crs_pass_test@db_test:5432/crs_vpn_test"
        else:
            test_db_url = "postgresql+asyncpg://crs_user_test:crs_pass_test@localhost:5433/crs_vpn_test"
    
    try:
        engine = create_async_engine(test_db_url, echo=False)
        
        # Создаем все таблицы
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        
        # Создаем sessionmaker для тестовой БД
        async_session_maker = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        
        # Мокируем SessionLocal во всех модулях
        from contextlib import asynccontextmanager
        
        @asynccontextmanager
        async def mock_session_local():
            async with async_session_maker() as session:
                yield session
        
        monkeypatch.setattr("app.db.session.SessionLocal", mock_session_local)
        monkeypatch.setattr("app.services.users.SessionLocal", mock_session_local)
        monkeypatch.setattr("app.services.subscriptions.SessionLocal", mock_session_local)
        monkeypatch.setattr("app.services.payments.yookassa.SessionLocal", mock_session_local)
        monkeypatch.setattr("app.services.stats.SessionLocal", mock_session_local)
        
        async with async_session_maker() as session:
            yield session
            await session.rollback()
        
        # Очищаем после теста
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        
        await engine.dispose()
    except Exception as e:
        # Если не удалось подключиться к БД, пропускаем тест
        import pytest
        pytest.skip(f"Не удалось подключиться к тестовой БД: {e}")


@pytest.fixture(scope="function")
def mock_bot():
    """Мок бота для тестов"""
    from unittest.mock import AsyncMock
    
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.answer_callback_query = AsyncMock()
    return bot


@pytest.fixture(scope="function")
def mock_remna_client():
    """Мок клиента Remna API"""
    from unittest.mock import AsyncMock
    
    client = AsyncMock()
    client.get_users = AsyncMock(return_value={
        "response": {
            "users": []
        }
    })
    client.get_user_subscription_url = AsyncMock(return_value="https://remna.example.com/subscription/123")
    client.create_user = AsyncMock(return_value={"uuid": "test-uuid-123"})
    client.close = AsyncMock()
    return client

