# tests/test_users.py
"""Тесты для сервиса работы с пользователями"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.users import (
    get_or_create_telegram_user,
    update_user_activity,
    get_user_active_subscription
)
from app.db.models import TelegramUser, Subscription


@pytest.mark.asyncio
async def test_get_or_create_telegram_user_new():
    """Тест создания нового пользователя"""
    with patch('app.services.users.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем отсутствие пользователя
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result
        
        # Мокируем refresh
        mock_session.refresh = AsyncMock()
        
        user = await get_or_create_telegram_user(
            telegram_id=123456789,
            username="test_user",
            first_name="Test",
            last_name="User"
        )
        
        assert user is not None
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_or_create_telegram_user_existing():
    """Тест обновления существующего пользователя"""
    with patch('app.services.users.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем существующего пользователя
        existing_user = TelegramUser(
            telegram_id=123456789,
            username="old_username",
            first_name="Old",
            last_name="Name"
        )
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_session.execute.return_value = mock_result
        
        mock_session.refresh = AsyncMock()
        
        user = await get_or_create_telegram_user(
            telegram_id=123456789,
            username="new_username",
            first_name="New",
            last_name="Name"
        )
        
        assert user is not None
        assert user.username == "new_username"
        mock_session.add.assert_not_called()
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_user_active_subscription():
    """Тест получения активной подписки"""
    with patch('app.services.users.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        subscription = Subscription(
            id=1,
            telegram_user_id=123456789,
            active=True,
            valid_until=datetime.utcnow() + timedelta(days=30),
            plan_code="premium"
        )
        sub_result = MagicMock()
        sub_result.scalar_one_or_none.return_value = subscription
        mock_session.execute.return_value = sub_result
        
        result = await get_user_active_subscription(123456789, use_cache=False)
        
        assert result is not None
        assert result.id == 1
        assert result.active is True


@pytest.mark.asyncio
async def test_get_user_active_subscription_no_user():
    """Тест получения подписки для несуществующего пользователя"""
    with patch('app.services.users.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = user_result
        
        result = await get_user_active_subscription(999999999)
        
        assert result is None






