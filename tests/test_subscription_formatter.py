"""Тесты для сервиса форматирования подписок"""
import pytest
from datetime import datetime, timedelta
from app.services.subscription_formatter import format_subscription_info, format_subscription_time
from app.db.models import Subscription


def test_format_subscription_info_active():
    """Тест форматирования активной подписки"""
    subscription = Subscription(
        id=1,
        plan_code="premium",
        plan_name="Премиум тариф",
        active=True,
        valid_until=datetime.utcnow() + timedelta(days=15)
    )
    
    result, has_subscription = format_subscription_info(subscription)
    
    assert has_subscription is True
    assert "Подписка" in result
    assert "15" in result or "дней" in result


def test_format_subscription_info_expired():
    """Тест форматирования истекшей подписки"""
    subscription = Subscription(
        id=1,
        plan_code="basic",
        plan_name="Базовый тариф",
        active=False,
        valid_until=datetime.utcnow() - timedelta(days=1)
    )
    
    result, has_subscription = format_subscription_info(subscription)
    
    assert has_subscription is True
    assert "Подписка" in result
    assert "истекла" in result.lower()


def test_format_subscription_info_no_expiry():
    """Тест форматирования подписки без срока действия"""
    subscription = Subscription(
        id=1,
        plan_code="premium",
        plan_name="Премиум тариф",
        active=True,
        valid_until=None
    )
    
    result, has_subscription = format_subscription_info(subscription)
    
    assert has_subscription is True
    assert "без ограничений" in result


def test_format_subscription_info_none():
    """Тест форматирования при отсутствии подписки"""
    result, has_subscription = format_subscription_info(None)
    
    assert has_subscription is False
    assert "не активна" in result.lower()


def test_format_subscription_time_future():
    """Тест форматирования времени для будущей даты"""
    future_date = datetime.utcnow() + timedelta(days=10)
    result, has_subscription = format_subscription_time(future_date)
    
    assert has_subscription is True
    assert "10" in result or "дней" in result


def test_format_subscription_time_past():
    """Тест форматирования времени для прошедшей даты"""
    past_date = datetime.utcnow() - timedelta(days=1)
    result, has_subscription = format_subscription_time(past_date)
    
    assert has_subscription is True
    assert "истекла" in result.lower()


def test_format_subscription_time_none():
    """Тест форматирования времени при отсутствии даты"""
    result, has_subscription = format_subscription_time(None)
    
    assert has_subscription is False
    assert "не активна" in result.lower()

