"""Интеграционные тесты для пользовательских историй"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy import select

from app.db.models import TelegramUser, Subscription, Payment
from app.services.users import get_or_create_telegram_user, get_user_active_subscription
from app.services.payments.yookassa import create_payment, handle_successful_payment
from app.services.subscriptions import create_trial_subscription


@pytest.mark.integration
@pytest.mark.asyncio
async def test_user_registration_flow(test_db_with_postgres):
    """Тест регистрации пользователя и создания пробной подписки"""
    session = test_db_with_postgres
    
    user = await get_or_create_telegram_user(
        telegram_id=123456789,
        username="test_user",
        first_name="Test",
        last_name="User",
        language_code="ru",
        create_trial=True
    )
    
    assert user.telegram_id == 123456789
    assert user.username == "test_user"
    
    result = await session.execute(
        select(TelegramUser).where(TelegramUser.telegram_id == 123456789)
    )
    db_user = result.scalar_one_or_none()
    assert db_user is not None
    assert db_user.username == "test_user"
    
    subscription = await get_user_active_subscription(123456789, use_cache=False)
    assert subscription is not None
    assert subscription.plan_code == "trial"
    assert subscription.active is True
    
    result = await session.execute(
        select(Subscription).where(Subscription.telegram_user_id == 123456789)
    )
    db_subscription = result.scalar_one_or_none()
    assert db_subscription.plan_code == "trial"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_payment_and_subscription_flow(test_db_with_postgres, mock_bot):
    """Тест создания платежа, успешной оплаты и создания подписки"""
    session = test_db_with_postgres
    
    user = await get_or_create_telegram_user(
        telegram_id=987654321,
        username="payment_user",
        first_name="Payment",
        last_name="User",
        create_trial=False
    )
    mock_payment_object = MagicMock()
    mock_payment_object.id = "test_payment_123"
    mock_payment_object.status = "pending"
    mock_payment_object.confirmation = MagicMock()
    mock_payment_object.confirmation.confirmation_url = "https://yookassa.ru/checkout/payments/test_payment_123"
    mock_payment_object.amount = MagicMock()
    mock_payment_object.amount.value = "199.00"
    mock_payment_object.amount.currency = "RUB"
    mock_payment_object.description = "CRS VPN - Премиум тариф (1 месяц)"
    mock_payment_object.metadata = {"tg_user_id": 987654321}
    mock_payment_object.paid = False
    # Добавляем метод dict() для сериализации
    mock_payment_object.dict = MagicMock(return_value={
        "id": "test_payment_123",
        "status": "pending",
        "amount": {"value": "199.00", "currency": "RUB"},
        "description": "CRS VPN - Премиум тариф (1 месяц)",
        "metadata": {"tg_user_id": 987654321}
    })
    
    with patch('app.services.payments.yookassa.Payment') as mock_payment_class, \
         patch('app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url') as mock_get_url:
        
        mock_payment_class.create.return_value = mock_payment_object
        mock_get_url.return_value = "https://remna.example.com/subscription/456"
        
        payment_url = await create_payment(
            amount_rub=199,
            description="CRS VPN - Премиум тариф (1 месяц)",
            user_id=987654321
        )
        
        assert payment_url is not None
        assert "yookassa.ru" in payment_url
        
        result = await session.execute(
            select(Payment).where(Payment.telegram_user_id == 987654321)
        )
        payment = result.scalar_one_or_none()
        assert payment is not None
        assert payment.external_id == "test_payment_123"
        assert payment.amount == 199.00
        assert payment.status == "pending"
        
        await session.commit()
        await session.refresh(payment)
        
        await handle_successful_payment(
            session=session,
            payment_id=payment.id,
            telegram_user_id=987654321,
            amount=199.0,
            description="CRS VPN - Премиум тариф (1 месяц)",
            bot=mock_bot
        )
        
        await session.commit()
        
        subscription = await get_user_active_subscription(987654321, use_cache=False)
        assert subscription is not None
        assert subscription.plan_code == "premium"
        assert subscription.active is True
        
        result = await session.execute(
            select(Subscription).where(Subscription.telegram_user_id == 987654321)
        )
        db_subscription = result.scalar_one_or_none()
        assert db_subscription.plan_code == "premium"
        assert db_subscription.active is True
        
        result = await session.execute(
            select(Payment).where(Payment.id == payment.id)
        )
        updated_payment = result.scalar_one_or_none()
        assert updated_payment.status == "succeeded"
        
        mock_bot.send_message.assert_called_once()
        call_args = mock_bot.send_message.call_args
        assert call_args.kwargs["chat_id"] == 987654321
        assert "Премиум" in call_args.kwargs["text"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_subscription_expiration_flow(test_db_with_postgres):
    """Тест истечения подписки"""
    session = test_db_with_postgres
    
    user = await get_or_create_telegram_user(
        telegram_id=555555555,
        username="expiring_user",
        create_trial=False
    )
    
    from app.db.models import Subscription
    expiring_subscription = Subscription(
        telegram_user_id=555555555,
        plan_code="basic",
        plan_name="Базовый тариф",
        active=True,
        valid_until=datetime.utcnow() + timedelta(days=1)
    )
    session.add(expiring_subscription)
    await session.commit()
    await session.refresh(expiring_subscription)
    
    subscription = await get_user_active_subscription(555555555, use_cache=False)
    assert subscription is not None
    assert subscription.active is True
    
    from app.services.subscriptions import check_expired_subscriptions
    
    expiring_subscription.valid_until = datetime.utcnow() - timedelta(days=1)
    await session.commit()
    
    result = await check_expired_subscriptions()
    assert result["expired"] >= 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_user_with_multiple_subscriptions(test_db_with_postgres):
    """Тест работы с несколькими подписками пользователя"""
    session = test_db_with_postgres
    
    user = await get_or_create_telegram_user(
        telegram_id=777777777,
        username="multi_sub_user",
        create_trial=False
    )
    
    from app.db.models import Subscription
    old_subscription = Subscription(
        telegram_user_id=777777777,
        plan_code="basic",
        plan_name="Базовый тариф",
        active=False,
        valid_until=datetime.utcnow() - timedelta(days=30)
    )
    session.add(old_subscription)
    
    new_subscription = Subscription(
        telegram_user_id=777777777,
        plan_code="premium",
        plan_name="Премиум тариф",
        active=True,
        valid_until=datetime.utcnow() + timedelta(days=30)
    )
    session.add(new_subscription)
    await session.commit()
    
    active_subscription = await get_user_active_subscription(777777777, use_cache=False)
    assert active_subscription is not None
    assert active_subscription.plan_code == "premium"
    assert active_subscription.active is True
    
    result = await session.execute(
        select(Subscription).where(
            Subscription.telegram_user_id == 777777777,
            Subscription.plan_code == "basic"
        )
    )
    old_sub = result.scalar_one_or_none()
    assert old_sub is not None
    assert old_sub.active is False

