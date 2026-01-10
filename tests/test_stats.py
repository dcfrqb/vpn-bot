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
        user1 = TelegramUser(telegram_id=1, username="user1", first_name="User", is_admin=False)
        user2 = TelegramUser(telegram_id=2, username="user2", first_name="User2", is_admin=False)
        
        # Мокируем результаты запросов
        # Первый запрос - count
        mock_total_result = MagicMock()
        mock_total_result.scalar.return_value = 2
        
        # Второй запрос - users_with_subs (JOIN запрос возвращает кортежи (user, subscription_plan))
        mock_users_result = MagicMock()
        # JOIN возвращает кортежи: (TelegramUser, subscription_plan)
        mock_users_result.all.return_value = [
            (user1, None),  # user1 без подписки
            (user2, "premium")  # user2 с подпиской premium
        ]
        
        mock_session.execute.side_effect = [
            mock_total_result,  # count запрос
            mock_users_result,  # users_with_subs JOIN запрос
        ]
        
        result = await get_users_list(page=1, page_size=10)
        
        assert result["total"] == 2
        assert len(result["users"]) == 2
        assert result["page"] == 1
        assert result["users"][0]["telegram_id"] == 1
        assert result["users"][0]["has_active_subscription"] is False
        assert result["users"][1]["telegram_id"] == 2
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
        
        # Мокируем результаты запросов
        # Первый запрос - count
        mock_total_result = MagicMock()
        mock_total_result.scalar.return_value = 2
        
        # Второй запрос - payments_with_users (JOIN запрос возвращает кортежи (payment, username))
        mock_payments_result = MagicMock()
        # JOIN возвращает кортежи: (Payment, username)
        mock_payments_result.all.return_value = [
            (payment1, "user1"),  # payment1 с username
            (payment2, "user2")   # payment2 с username
        ]
        
        mock_session.execute.side_effect = [
            mock_total_result,      # count запрос
            mock_payments_result,   # payments_with_users JOIN запрос
        ]
        
        result = await get_payments_list(page=1, page_size=10)
        
        assert result["total"] == 2
        assert len(result["payments"]) == 2
        assert result["payments"][0]["amount"] == 99.0
        assert result["payments"][0]["status"] == "succeeded"
        assert result["payments"][0]["username"] == "user1"
        assert result["payments"][1]["status"] == "pending"
        assert result["payments"][1]["username"] == "user2"


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
        
        # Мокируем результаты запросов
        mock_total_result = MagicMock()
        mock_total_result.scalar.return_value = 1
        
        # JOIN запрос возвращает кортежи (payment, username)
        mock_payments_result = MagicMock()
        mock_payments_result.all.return_value = [
            (payment, "user1")
        ]
        
        mock_session.execute.side_effect = [
            mock_total_result,      # count запрос
            mock_payments_result,   # payments_with_users JOIN запрос
        ]
        
        result = await get_payments_list(page=1, page_size=10, status="succeeded")
        
        assert result["total"] == 1
        assert result["status_filter"] == "succeeded"
        assert result["payments"][0]["status"] == "succeeded"
        assert result["payments"][0]["username"] == "user1"






