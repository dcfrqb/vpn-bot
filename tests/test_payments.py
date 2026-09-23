# tests/test_payments.py
"""Тесты для сервиса работы с платежами Yookassa"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from typing import Dict, Any

from app.services.payments.yookassa import (
    create_payment,
    check_payment_status,
)
from app.services.payments.errors import ProvisioningPendingError
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


def _payment_json(p) -> dict:
    return {"id": p.id, "status": p.status,
            "confirmation": {"type": "redirect", "confirmation_url": p.confirmation.confirmation_url}}


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
    with patch('app.services.payments.yookassa._create_yookassa_payment', new_callable=AsyncMock) as mock_create, \
         patch('app.services.payments.yookassa.SessionLocal') as mock_session_local, \
         patch('app.services.payments.yookassa.settings') as mock_settings:
        
        # Мокируем настройки YooKassa
        mock_settings.YOOKASSA_SHOP_ID = "test_shop_id"
        mock_settings.YOOKASSA_API_KEY = "test_api_key"
        mock_settings.YOOKASSA_RETURN_URL = "https://example.com/return"
        
        # Настройка моков (3.0: async-клиент возвращает JSON YooKassa)
        mock_create.return_value = _payment_json(mock_payment_object)
        
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        mock_session_local.return_value.__aexit__.return_value = None
        
        # Мокируем запрос к БД
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # Платеж не существует
        mock_session.execute.return_value = mock_result
        
        # Вызываем функцию
        payment_url, external_id = await create_payment(
            amount_rub=129,
            description="CRS VPN - Lite (1 месяц)",
            user_id=123456789,
            plan_code="lite",
            period_months=1,
        )
        
        # Проверки
        assert payment_url == "https://yookassa.ru/checkout/payments/test_payment_123"
        assert external_id == "test_payment_123"
        mock_create.assert_awaited_once()
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_create_payment_without_db(mock_payment_object):
    """Тест создания платежа без БД — теперь должен вызывать исключение"""
    with patch('app.services.payments.yookassa._create_yookassa_payment', new_callable=AsyncMock) as mock_create, \
         patch('app.services.payments.yookassa.SessionLocal', None), \
         patch('app.services.payments.yookassa.settings') as mock_settings:
        
        mock_settings.YOOKASSA_SHOP_ID = "test_shop_id"
        mock_settings.YOOKASSA_API_KEY = "test_api_key"
        mock_settings.YOOKASSA_RETURN_URL = "https://example.com/return"
        
        mock_create.return_value = _payment_json(mock_payment_object)
        
        with pytest.raises(ValueError, match="БД не настроена"):
            await create_payment(
                amount_rub=129,
                description="CRS VPN - Lite (1 месяц)",
                user_id=123456789,
                plan_code="lite",
                period_months=1,
            )


@pytest.mark.asyncio
async def test_create_payment_missing_config():
    """Тест создания платежа без настроек"""
    with patch('app.services.payments.yookassa.settings') as mock_settings:
        mock_settings.YOOKASSA_SHOP_ID = None
        mock_settings.YOOKASSA_API_KEY = None
        
        with pytest.raises(ValueError, match="YOOKASSA_SHOP_ID и YOOKASSA_API_KEY должны быть настроены"):
            await create_payment(129, "Test", 123456789, plan_code="lite", period_months=1)


# test_process_payment_webhook_pending: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)


# test_process_payment_webhook_succeeded: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)


# test_process_payment_webhook_succeeded_idempotent: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)


# test_process_payment_webhook_resyncs_split_state: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)


# test_process_payment_webhook_invalid_transition_canceled_to_succeeded: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)


# test_process_payment_webhook_missing_user_id: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)


# test_process_payment_webhook_empty_data: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)


def _gateway_with(handler):
    import httpx

    from app.infra.yookassa import YooKassaClient, YooKassaGateway

    return YooKassaGateway(client_factory=lambda: YooKassaClient(
        "shop", "key", transport=httpx.MockTransport(handler), sleep=AsyncMock()))


@pytest.mark.asyncio
async def test_check_payment_status_success():
    """Статус платежа через async-шлюз (3.0: без SDK)"""
    import httpx

    def handler(request):
        assert request.url.path == "/v3/payments/test_payment_456"
        return httpx.Response(200, json={
            "id": "test_payment_456", "status": "succeeded", "paid": True,
            "amount": {"value": "249.00", "currency": "RUB"},
            "description": "CRS VPN", "metadata": {"tg_user_id": "123456789"},
        })

    with patch('app.infra.yookassa.default_gateway', return_value=_gateway_with(handler)), \
         patch('app.services.payments.yookassa.settings') as mock_settings:
        mock_settings.YOOKASSA_SHOP_ID = "test_shop_id"
        mock_settings.YOOKASSA_API_KEY = "test_api_key"

        result = await check_payment_status("test_payment_456")

        assert result is not None
        assert result["id"] == "test_payment_456"
        assert result["status"] == "succeeded"
        assert result["amount"] == 249.00
        assert result["currency"] == "RUB"
        assert result["paid"] is True


@pytest.mark.asyncio
async def test_check_payment_status_not_found():
    """Несуществующий платеж -> {"error": "not_found"}"""
    import httpx

    with patch('app.infra.yookassa.default_gateway',
               return_value=_gateway_with(lambda r: httpx.Response(404, json={"type": "error"}))), \
         patch('app.services.payments.yookassa.settings') as mock_settings:
        mock_settings.YOOKASSA_SHOP_ID = "test"
        mock_settings.YOOKASSA_API_KEY = "test"

        result = await check_payment_status("non_existent_payment")

        assert result is not None
        assert result.get("error") == "not_found"


@pytest.mark.asyncio
async def test_check_payment_status_missing_config():
    """Тест проверки статуса без настроек"""
    with patch('app.services.payments.yookassa.settings') as mock_settings:
        mock_settings.YOOKASSA_SHOP_ID = None
        mock_settings.YOOKASSA_API_KEY = None
        
        result = await check_payment_status("test_payment")
        
        assert result is None


def _build_basic_handle_payment_mocks(amount: float = 99.0):
    """Готовит набор моков для тестов handle_successful_payment.

    Возвращает (mock_session, captured_state, helpers). captured_state — словарь
    с in-progress subscription, обновляемой по ходу теста. Вторая и далее
    queries Subscription возвращают эту же subscription (а не None), эмулируя
    persistance.
    """
    user = TelegramUser(
        telegram_id=123456789,
        username="test_user",
        remna_user_id="test-remna-uuid",  # critical: иначе post-Phase B silent-failure check сработает
    )
    payment_db = PaymentModel(
        id=1,
        telegram_user_id=123456789,
        external_id="ext-1",
        amount=amount,
        currency="RUB",
        status="succeeded",
        payment_metadata={},
    )

    state = {"subscription": None}

    mock_user_result = MagicMock()
    mock_user_result.scalar_one_or_none.return_value = user

    mock_payment_result = MagicMock()
    mock_payment_result.scalar_one_or_none.return_value = payment_db

    def make_sub_result():
        r = MagicMock()
        r.scalar_one_or_none.return_value = state["subscription"]
        return r

    async def mock_execute(query):
        s = str(query).lower()
        if "from subscriptions" in s:
            return make_sub_result()
        if "from telegram_users" in s:
            return mock_user_result
        if "from payments" in s:
            return mock_payment_result
        return make_sub_result()

    def fake_add(obj):
        if isinstance(obj, Subscription):
            obj.id = 42
            state["subscription"] = obj

    async def fake_refresh(obj):
        if isinstance(obj, Subscription) and not getattr(obj, "id", None):
            obj.id = 42

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=mock_execute)
    mock_session.add = MagicMock(side_effect=fake_add)
    mock_session.refresh = AsyncMock(side_effect=fake_refresh)
    return mock_session, state, {"user": user, "payment": payment_db}


# test_handle_successful_payment_basic: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)


# test_handle_successful_payment_premium: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)


# test_handle_successful_payment_idempotent_when_synced: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)


# test_handle_successful_payment_user_not_found: removed in 3.0 with the 2.x provisioning (tests/money/test_webhook.py and test_fulfillment.py)

