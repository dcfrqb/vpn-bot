# tests/test_payments.py
"""Тесты для сервиса работы с платежами Yookassa"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from typing import Dict, Any

from app.services.payments.yookassa import (
    create_payment,
    process_payment_webhook,
    check_payment_status,
    handle_successful_payment
)
from app.db.models import Payment as PaymentModel, Subscription, TelegramUser
from app.config import settings


@pytest.fixture
def mock_payment_object():
    """Мок объекта платежа от Yookassa"""
    payment = Mock()
    payment.id = "test_payment_123"
    payment.status = "pending"
    payment.confirmation = Mock()
    payment.confirmation.confirmation_url = "https://yookassa.ru/checkout/payments/test_payment_123"
    payment.amount = Mock()
    payment.amount.value = "99.00"
    payment.amount.currency = "RUB"
    payment.description = "CRS VPN - Базовый тариф (30 дней)"
    payment.metadata = {"tg_user_id": 123456789}
    payment.paid = False
    payment.created_at = datetime.utcnow()
    payment.captured_at = None
    payment.dict = lambda: {"id": payment.id, "status": payment.status}
    return payment


@pytest.fixture
def mock_succeeded_payment():
    """Мок успешного платежа"""
    payment = Mock()
    payment.id = "test_payment_456"
    payment.status = "succeeded"
    payment.amount = Mock()
    payment.amount.value = "249.00"
    payment.amount.currency = "RUB"
    payment.description = "CRS VPN - Премиум тариф (30 дней)"
    payment.metadata = {"tg_user_id": 123456789}
    payment.paid = True
    payment.created_at = datetime.utcnow()
    payment.captured_at = datetime.utcnow()
    return payment


@pytest.fixture
def webhook_data_pending():
    """Данные webhook для платежа в статусе pending"""
    return {
        "type": "notification",
        "event": "payment.waiting_for_capture",
        "object": {
            "id": "test_payment_123",
            "status": "pending",
            "amount": {
                "value": "99.00",
                "currency": "RUB"
            },
            "description": "CRS VPN - Базовый тариф (30 дней)",
            "metadata": {
                "tg_user_id": "123456789"
            },
            "created_at": datetime.utcnow().isoformat(),
            "paid": False
        }
    }


@pytest.fixture
def webhook_data_succeeded():
    """Данные webhook для успешного платежа"""
    return {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": "test_payment_456",
            "status": "succeeded",
            "amount": {
                "value": "249.00",
                "currency": "RUB"
            },
            "description": "CRS VPN - Премиум тариф (30 дней)",
            "metadata": {
                "tg_user_id": "123456789"
            },
            "created_at": datetime.utcnow().isoformat(),
            "paid": True,
            "captured_at": datetime.utcnow().isoformat()
        }
    }


@pytest.mark.asyncio
async def test_create_payment_success(mock_payment_object):
    """Тест успешного создания платежа"""
    with patch('app.services.payments.yookassa.Payment') as mock_payment_class, \
         patch('app.services.payments.yookassa.SessionLocal') as mock_session_local, \
         patch('app.services.payments.yookassa.settings') as mock_settings:
        
        # Мокируем настройки YooKassa
        mock_settings.YOOKASSA_SHOP_ID = "test_shop_id"
        mock_settings.YOOKASSA_API_KEY = "test_api_key"
        mock_settings.YOOKASSA_RETURN_URL = "https://example.com/return"
        
        # Настройка моков
        mock_payment_class.create.return_value = mock_payment_object
        
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        mock_session_local.return_value.__aexit__.return_value = None
        
        # Мокируем запрос к БД
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # Платеж не существует
        mock_session.execute.return_value = mock_result
        
        # Вызываем функцию
        payment_url = await create_payment(
            amount_rub=99,
            description="CRS VPN - Базовый тариф (30 дней)",
            user_id=123456789
        )
        
        # Проверки
        assert payment_url == "https://yookassa.ru/checkout/payments/test_payment_123"
        mock_payment_class.create.assert_called_once()
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_create_payment_without_db(mock_payment_object):
    """Тест создания платежа без БД"""
    with patch('app.services.payments.yookassa.Payment') as mock_payment_class, \
         patch('app.services.payments.yookassa.SessionLocal', None), \
         patch('app.services.payments.yookassa.settings') as mock_settings:
        
        # Мокируем настройки YooKassa
        mock_settings.YOOKASSA_SHOP_ID = "test_shop_id"
        mock_settings.YOOKASSA_API_KEY = "test_api_key"
        mock_settings.YOOKASSA_RETURN_URL = "https://example.com/return"
        
        mock_payment_class.create.return_value = mock_payment_object
        
        payment_url = await create_payment(
            amount_rub=99,
            description="CRS VPN - Базовый тариф (30 дней)",
            user_id=123456789
        )
        
        assert payment_url == "https://yookassa.ru/checkout/payments/test_payment_123"


@pytest.mark.asyncio
async def test_create_payment_missing_config():
    """Тест создания платежа без настроек"""
    with patch('app.services.payments.yookassa.settings') as mock_settings:
        mock_settings.YOOKASSA_SHOP_ID = None
        mock_settings.YOOKASSA_API_KEY = None
        
        with pytest.raises(ValueError, match="YOOKASSA_SHOP_ID и YOOKASSA_API_KEY должны быть настроены"):
            await create_payment(99, "Test", 123456789)


@pytest.mark.asyncio
async def test_process_payment_webhook_pending(webhook_data_pending):
    """Тест обработки webhook для платежа в статусе pending"""
    with patch('app.services.payments.yookassa.SessionLocal') as mock_session_local, \
         patch('app.services.payments.yookassa.WebhookNotification') as mock_notification:
        
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем уведомление
        mock_notif_obj = Mock()
        mock_notif_obj.object = Mock()
        mock_notif_obj.object.id = "test_payment_123"
        mock_notif_obj.object.status = "pending"
        mock_notif_obj.object.amount.value = "99.00"
        mock_notif_obj.object.amount.currency = "RUB"
        mock_notif_obj.object.description = "CRS VPN - Базовый тариф (30 дней)"
        mock_notif_obj.object.metadata = {"tg_user_id": "123456789"}
        mock_notification.return_value = mock_notif_obj
        
        # Мокируем запрос к БД
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # Платеж не существует
        mock_session.execute.return_value = mock_result
        
        mock_bot = AsyncMock()
        
        result = await process_payment_webhook(webhook_data_pending, mock_bot)
        
        assert result is True
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        # Для pending платежа не должна вызываться handle_successful_payment
        mock_bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_process_payment_webhook_succeeded(webhook_data_succeeded):
    """Тест обработки webhook для успешного платежа"""
    with patch('app.services.payments.yookassa.SessionLocal') as mock_session_local, \
         patch('app.services.payments.yookassa.WebhookNotification') as mock_notification, \
         patch('app.services.payments.yookassa.handle_successful_payment') as mock_handle, \
         patch('app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url') as mock_get_url:
        
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем уведомление
        mock_notif_obj = Mock()
        mock_notif_obj.object = Mock()
        mock_notif_obj.object.id = "test_payment_456"
        mock_notif_obj.object.status = "succeeded"
        mock_notif_obj.object.amount.value = "249.00"
        mock_notif_obj.object.amount.currency = "RUB"
        mock_notif_obj.object.description = "CRS VPN - Премиум тариф (30 дней)"
        mock_notif_obj.object.metadata = {"tg_user_id": "123456789"}
        mock_notification.return_value = mock_notif_obj
        
        # Мокируем платеж в БД
        payment_db = PaymentModel(
            id=1,
            telegram_user_id=123456789,
            external_id="test_payment_456",
            amount=249.00,
            currency="RUB",
            status="pending"
        )
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # Платеж не существует
        mock_session.execute.return_value = mock_result
        
        # Мокируем refresh для нового платежа
        async def mock_refresh(obj):
            obj.id = 1
        mock_session.refresh = AsyncMock(side_effect=mock_refresh)
        
        mock_bot = AsyncMock()
        mock_handle.return_value = None
        mock_get_url.return_value = "https://remna.example.com/subscription/123"
        
        result = await process_payment_webhook(webhook_data_succeeded, mock_bot)
        
        assert result is True
        mock_session.add.assert_called_once()
        assert mock_session.commit.call_count >= 1
        # handle_successful_payment должна быть вызвана для succeeded платежа
        mock_handle.assert_called_once()


@pytest.mark.asyncio
async def test_process_payment_webhook_missing_user_id():
    """Тест обработки webhook без telegram_user_id"""
    webhook_data = {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": "test_payment_789",
            "status": "succeeded",
            "amount": {"value": "99.00", "currency": "RUB"},
            "description": "Test",
            "metadata": {}  # Нет tg_user_id
        }
    }
    
    with patch('app.services.payments.yookassa.WebhookNotification') as mock_notification:
        mock_notif_obj = Mock()
        mock_notif_obj.object = Mock()
        mock_notif_obj.object.id = "test_payment_789"
        mock_notif_obj.object.status = "succeeded"
        mock_notif_obj.object.amount.value = "99.00"
        mock_notif_obj.object.amount.currency = "RUB"
        mock_notif_obj.object.description = "Test"
        mock_notif_obj.object.metadata = {}
        mock_notification.return_value = mock_notif_obj
        
        mock_bot = AsyncMock()
        
        result = await process_payment_webhook(webhook_data, mock_bot)
        
        assert result is False


@pytest.mark.asyncio
async def test_process_payment_webhook_empty_data():
    """Тест обработки пустого webhook"""
    mock_bot = AsyncMock()
    
    result = await process_payment_webhook({}, mock_bot)
    
    assert result is False


@pytest.mark.asyncio
async def test_check_payment_status_success(mock_succeeded_payment):
    """Тест проверки статуса платежа"""
    with patch('app.services.payments.yookassa.Payment') as mock_payment_class, \
         patch('app.services.payments.yookassa.settings') as mock_settings:
        
        # Мокируем настройки YooKassa
        mock_settings.YOOKASSA_SHOP_ID = "test_shop_id"
        mock_settings.YOOKASSA_API_KEY = "test_api_key"
        
        mock_payment_class.find_one.return_value = mock_succeeded_payment
        
        result = await check_payment_status("test_payment_456")
        
        assert result is not None
        assert result["id"] == "test_payment_456"
        assert result["status"] == "succeeded"
        assert result["amount"] == 249.00
        assert result["currency"] == "RUB"
        assert result["paid"] is True


@pytest.mark.asyncio
async def test_check_payment_status_not_found():
    """Тест проверки статуса несуществующего платежа"""
    with patch('app.services.payments.yookassa.Payment') as mock_payment_class:
        mock_payment_class.find_one.return_value = None
        
        result = await check_payment_status("non_existent_payment")
        
        assert result is None


@pytest.mark.asyncio
async def test_check_payment_status_missing_config():
    """Тест проверки статуса без настроек"""
    with patch('app.services.payments.yookassa.settings') as mock_settings:
        mock_settings.YOOKASSA_SHOP_ID = None
        mock_settings.YOOKASSA_API_KEY = None
        
        result = await check_payment_status("test_payment")
        
        assert result is None


@pytest.mark.asyncio
async def test_handle_successful_payment_basic():
    """Тест обработки успешного платежа для базового тарифа"""
    with patch('app.services.payments.yookassa.SessionLocal') as mock_session_local, \
         patch('app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url') as mock_get_url:
        
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем пользователя
        user = TelegramUser(telegram_id=123456789, username="test_user")
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = user
        
        # Мокируем подписку (не существует)
        mock_sub_result = MagicMock()
        mock_sub_result.scalar_one_or_none.return_value = None
        
        # Мокируем платеж
        payment_db = PaymentModel(id=1, telegram_user_id=123456789)
        mock_payment_result = MagicMock()
        mock_payment_result.scalar_one_or_none.return_value = payment_db
        
        # Настраиваем execute для разных запросов
        async def mock_execute(query):
            # Определяем тип запроса по содержимому
            query_str = str(query)
            if "telegram_users" in query_str or "TelegramUser" in query_str:
                return mock_user_result
            elif "subscriptions" in query_str or "Subscription" in query_str:
                return mock_sub_result
            elif "payments" in query_str or "Payment" in query_str:
                return mock_payment_result
            return mock_sub_result
        
        mock_session.execute = AsyncMock(side_effect=mock_execute)
        
        # Мокируем refresh
        async def mock_refresh(obj):
            if hasattr(obj, 'id') and obj.id is None:
                obj.id = 1
        mock_session.refresh = AsyncMock(side_effect=mock_refresh)
        
        mock_get_url.return_value = "https://remna.example.com/subscription/123"
        
        mock_bot = AsyncMock()
        
        await handle_successful_payment(
            session=mock_session,
            payment_id=1,
            telegram_user_id=123456789,
            amount=99.0,
            description="CRS VPN - Базовый тариф (30 дней)",
            bot=mock_bot
        )
        
        # Проверяем, что подписка создана
        mock_session.add.assert_called()
        assert mock_session.commit.call_count >= 2
        mock_bot.send_message.assert_called_once()
        
        # Проверяем сообщение
        call_args = mock_bot.send_message.call_args
        assert call_args.kwargs["chat_id"] == 123456789
        assert "Базовый" in call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_handle_successful_payment_premium():
    """Тест обработки успешного платежа для премиум тарифа"""
    with patch('app.services.payments.yookassa.SessionLocal') as mock_session_local, \
         patch('app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url') as mock_get_url:
        
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем пользователя
        user = TelegramUser(telegram_id=123456789, username="test_user")
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = user
        
        # Мокируем подписку (не существует)
        mock_sub_result = MagicMock()
        mock_sub_result.scalar_one_or_none.return_value = None
        
        # Мокируем платеж
        payment_db = PaymentModel(id=1, telegram_user_id=123456789)
        mock_payment_result = MagicMock()
        mock_payment_result.scalar_one_or_none.return_value = payment_db
        
        # Настраиваем execute для разных запросов
        async def mock_execute(query):
            query_str = str(query)
            if "telegram_users" in query_str or "TelegramUser" in query_str:
                return mock_user_result
            elif "subscriptions" in query_str or "Subscription" in query_str:
                return mock_sub_result
            elif "payments" in query_str or "Payment" in query_str:
                return mock_payment_result
            return mock_sub_result
        
        mock_session.execute = AsyncMock(side_effect=mock_execute)
        
        # Мокируем refresh
        async def mock_refresh(obj):
            if hasattr(obj, 'id') and obj.id is None:
                obj.id = 1
        mock_session.refresh = AsyncMock(side_effect=mock_refresh)
        
        mock_get_url.return_value = "https://remna.example.com/subscription/456"
        
        mock_bot = AsyncMock()
        
        await handle_successful_payment(
            session=mock_session,
            payment_id=1,
            telegram_user_id=123456789,
            amount=249.0,
            description="CRS VPN - Премиум тариф (30 дней)",
            bot=mock_bot
        )
        
        # Проверяем сообщение
        call_args = mock_bot.send_message.call_args
        assert "Премиум" in call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_handle_successful_payment_user_not_found():
    """Тест обработки успешного платежа для несуществующего пользователя"""
    with patch('app.services.payments.yookassa.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем отсутствие пользователя
        mock_user_result = MagicMock()
        mock_user_result.scalar_one_or_none.return_value = None
        
        async def mock_execute(query):
            return mock_user_result
        
        mock_session.execute = AsyncMock(side_effect=mock_execute)
        
        mock_bot = AsyncMock()
        
        await handle_successful_payment(
            session=mock_session,
            payment_id=1,
            telegram_user_id=999999999,
            amount=99.0,
            description="Test",
            bot=mock_bot
        )
        
        # Пользователь не найден, сообщение не отправляется
        mock_bot.send_message.assert_not_called()

