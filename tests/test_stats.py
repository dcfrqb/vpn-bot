# tests/test_stats.py
"""Тесты для сервиса статистики"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from app.services.stats import get_statistics, get_users_list, get_payments_list
from app.db.models import TelegramUser, Subscription, Payment


@pytest.mark.asyncio
async def test_get_statistics():
    """Тест получения статистики"""
    with patch('app.services.stats.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем результаты запросов
        mock_session.execute.side_effect = [
            MagicMock(scalar=lambda: 10),  # total_users
            MagicMock(scalar=lambda: 2),   # today_users
            MagicMock(scalar=lambda: 5),   # active_subscriptions
            MagicMock(scalar=lambda: 20),  # total_payments
            MagicMock(scalar=lambda: 3),   # today_payments
            MagicMock(scalar=lambda: 5000.0),  # total_revenue
            MagicMock(scalar=lambda: 500.0),   # today_revenue
        ]
        
        result = await get_statistics()
        
        assert result["total_users"] == 10
        assert result["active_subscriptions"] == 5
        assert result["total_payments"] == 20
        assert result["total_revenue"] == 5000.0
        assert result["today_users"] == 2
        assert result["today_payments"] == 3
        assert result["today_revenue"] == 500.0


@pytest.mark.asyncio
async def test_get_users_list():
    """Тест получения списка пользователей"""
    with patch('app.services.stats.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем пользователей
        user1 = TelegramUser(telegram_id=1, username="user1")
        user2 = TelegramUser(telegram_id=2, username="user2")
        
        mock_total_result = MagicMock()
        mock_total_result.scalar.return_value = 2
        
        mock_users_result = MagicMock()
        mock_users_result.scalars.return_value.all.return_value = [user1, user2]
        
        # Мокируем подписки для каждого пользователя
        mock_sub_result = MagicMock()
        mock_sub_result.scalar_one_or_none.side_effect = [
            None,  # для user1 нет подписки
            Subscription(plan_code="premium", active=True)  # для user2 есть подписка
        ]
        
        mock_session.execute.side_effect = [
            mock_total_result,  # count
            mock_users_result,  # users
            mock_sub_result,    # subscription для user1
            mock_sub_result,    # subscription для user2
        ]
        
        result = await get_users_list(page=1, page_size=10)
        
        assert result["total"] == 2
        assert len(result["users"]) == 2
        assert result["page"] == 1
        assert result["users"][0]["telegram_id"] == 1
        assert result["users"][1]["has_active_subscription"] is True


@pytest.mark.asyncio
async def test_get_payments_list():
    """Тест получения списка платежей"""
    with patch('app.services.stats.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем платежи
        payment1 = Payment(
            id=1,
            telegram_user_id=123456789,
            amount=99.0,
            currency="RUB",
            status="succeeded",
            provider="yookassa",
            external_id="test_1"
        )
        payment2 = Payment(
            id=2,
            telegram_user_id=987654321,
            amount=249.0,
            currency="RUB",
            status="pending",
            provider="yookassa",
            external_id="test_2"
        )
        
        user1 = TelegramUser(telegram_id=123456789, username="user1")
        user2 = TelegramUser(telegram_id=987654321, username="user2")
        
        mock_total_result = MagicMock()
        mock_total_result.scalar.return_value = 2
        
        mock_payments_result = MagicMock()
        mock_payments_result.scalars.return_value.all.return_value = [payment1, payment2]
        
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.side_effect = [user1, user2]
        
        mock_session.execute.side_effect = [
            mock_total_result,      # count
            mock_payments_result,   # payments
            mock_user_result,       # user для payment1
            mock_user_result,       # user для payment2
        ]
        
        result = await get_payments_list(page=1, page_size=10)
        
        assert result["total"] == 2
        assert len(result["payments"]) == 2
        assert result["payments"][0]["amount"] == 99.0
        assert result["payments"][0]["status"] == "succeeded"
        assert result["payments"][1]["status"] == "pending"


@pytest.mark.asyncio
async def test_get_payments_list_with_filter():
    """Тест получения списка платежей с фильтром по статусу"""
    with patch('app.services.stats.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        payment = Payment(
            id=1,
            telegram_user_id=123456789,
            amount=99.0,
            currency="RUB",
            status="succeeded",
            provider="yookassa",
            external_id="test_1"
        )
        
        user = TelegramUser(telegram_id=123456789, username="user1")
        
        mock_total_result = MagicMock()
        mock_total_result.scalar.return_value = 1
        
        mock_payments_result = MagicMock()
        mock_payments_result.scalars.return_value.all.return_value = [payment]
        
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = user
        
        mock_session.execute.side_effect = [
            mock_total_result,
            mock_payments_result,
            mock_user_result,
        ]
        
        result = await get_payments_list(page=1, page_size=10, status="succeeded")
        
        assert result["total"] == 1
        assert result["status_filter"] == "succeeded"
        assert result["payments"][0]["status"] == "succeeded"






