# tests/test_subscriptions.py
"""Тесты для сервиса работы с подписками"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.subscriptions import (
    check_expired_subscriptions,
    get_subscriptions_expiring_soon,
    send_expiration_notification
)
from app.db.models import Subscription, TelegramUser


@pytest.mark.asyncio
async def test_check_expired_subscriptions():
    """Тест проверки истекших подписок"""
    with patch('app.services.subscriptions.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем истекшую подписку
        expired_sub = Subscription(
            id=1,
            telegram_user_id=123456789,
            active=True,
            valid_until=datetime.utcnow() - timedelta(days=1),
            plan_code="premium"
        )
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [expired_sub]
        mock_session.execute.return_value = mock_result
        
        result = await check_expired_subscriptions()
        
        assert result["expired"] == 1
        assert expired_sub.active is False
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_subscriptions_expiring_soon():
    """Тест получения подписок, истекающих скоро"""
    with patch('app.services.subscriptions.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        now = datetime.utcnow()
        user = TelegramUser(telegram_id=123456789, username="test_user")
        subscription = Subscription(
            id=1,
            telegram_user_id=123456789,
            active=True,
            valid_until=now + timedelta(days=3),
            plan_code="premium"
        )
        
        mock_result = MagicMock()
        mock_result.all.return_value = [(subscription, user)]
        mock_session.execute.return_value = mock_result
        
        result = await get_subscriptions_expiring_soon(3)
        
        assert len(result) == 1
        assert result[0]["subscription"].id == 1
        assert result[0]["user"].telegram_id == 123456789
        assert result[0]["days_left"] >= 2
        assert result[0]["days_left"] <= 3


@pytest.mark.asyncio
async def test_send_expiration_notification():
    """Тест отправки уведомления об истечении"""
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()
    
    subscription = Subscription(
        id=1,
        telegram_user_id=123456789,
        active=True,
        valid_until=datetime.utcnow() + timedelta(days=3),
        plan_code="premium",
        plan_name="Премиум"
    )
    
    result = await send_expiration_notification(
        mock_bot,
        123456789,
        3,
        subscription
    )
    
    assert result is True
    mock_bot.send_message.assert_called_once()
    call_args = mock_bot.send_message.call_args
    assert call_args.kwargs["chat_id"] == 123456789
    assert "3 дня" in call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_send_expiration_notification_today():
    """Тест отправки уведомления об истечении сегодня"""
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()
    
    subscription = Subscription(
        id=1,
        telegram_user_id=123456789,
        active=True,
        valid_until=datetime.utcnow() + timedelta(hours=12),
        plan_code="premium"
    )
    
    result = await send_expiration_notification(
        mock_bot,
        123456789,
        0,
        subscription
    )
    
    assert result is True
    call_args = mock_bot.send_message.call_args
    assert "сегодня" in call_args.kwargs["text"].lower() or "истекает сегодня" in call_args.kwargs["text"]






