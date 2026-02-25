# tests/test_recovery.py
"""Тесты для recovery: retry_needs_provisioning, recheck_pending_payments, recheck_single_payment"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.payments.recovery import (
    retry_needs_provisioning,
    recheck_pending_payments,
    recheck_single_payment,
    PROVISIONING_FALLBACK_MINUTES,
)
from app.db.models import Payment as PaymentModel


@pytest.mark.asyncio
async def test_retry_needs_provisioning_with_flag():
    """retry_needs_provisioning обрабатывает платежи с needs_provisioning=True"""
    payment = PaymentModel(
        id=1,
        telegram_user_id=123456789,
        external_id="ext-1",
        amount=99,
        currency="RUB",
        status="succeeded",
        subscription_id=None,
        payment_metadata={"needs_provisioning": True},
        created_at=datetime.utcnow() - timedelta(hours=1),
    )

    with patch('app.services.payments.recovery.SessionLocal') as mock_sl, \
         patch('app.services.payments.yookassa.handle_successful_payment') as mock_handle:
        mock_session = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [payment]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_bot = AsyncMock()
        result = await retry_needs_provisioning(mock_bot)

        assert result["processed"] == 1
        mock_handle.assert_called_once()


@pytest.mark.asyncio
async def test_retry_needs_provisioning_fallback_old_payment():
    """retry_needs_provisioning обрабатывает succeeded без подписки и без флага, если платёж старше N минут"""
    payment = PaymentModel(
        id=2,
        telegram_user_id=123456789,
        external_id="ext-2",
        amount=99,
        currency="RUB",
        status="succeeded",
        subscription_id=None,
        payment_metadata={},
        created_at=datetime.utcnow() - timedelta(minutes=PROVISIONING_FALLBACK_MINUTES + 5),
    )

    with patch('app.services.payments.recovery.SessionLocal') as mock_sl, \
         patch('app.services.payments.yookassa.handle_successful_payment') as mock_handle:
        mock_session = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [payment]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_bot = AsyncMock()
        result = await retry_needs_provisioning(mock_bot)

        assert result["processed"] == 1
        mock_handle.assert_called_once()


@pytest.mark.asyncio
async def test_retry_needs_provisioning_skips_recent_without_flag():
    """retry_needs_provisioning НЕ обрабатывает недавние платежи без needs_provisioning"""
    payment = PaymentModel(
        id=3,
        telegram_user_id=123456789,
        external_id="ext-3",
        amount=99,
        currency="RUB",
        status="succeeded",
        subscription_id=None,
        payment_metadata={},
        created_at=datetime.utcnow() - timedelta(minutes=5),
    )

    with patch('app.services.payments.recovery.SessionLocal') as mock_sl, \
         patch('app.services.payments.yookassa.handle_successful_payment') as mock_handle:
        mock_session = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [payment]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_bot = AsyncMock()
        result = await retry_needs_provisioning(mock_bot)

        assert result["processed"] == 0
        mock_handle.assert_not_called()


# --- recheck_single_payment ---


@pytest.mark.asyncio
async def test_recheck_single_payment_succeeded_provisioned():
    """Кнопка 'Проверить оплату': YooKassa returns succeeded → payment_db обновлён → provisioning вызван 1 раз"""
    payment = PaymentModel(
        id=10,
        telegram_user_id=123456789,
        external_id="ext-pending-1",
        amount=99,
        currency="RUB",
        status="pending",
        subscription_id=None,
        payment_metadata={"plan_code": "basic", "period_months": 1},
        created_at=datetime.utcnow(),
    )

    with patch('app.services.payments.recovery.SessionLocal') as mock_sl, \
         patch('app.services.payments.yookassa.check_payment_status') as mock_check, \
         patch('app.services.payments.yookassa.handle_successful_payment') as mock_handle:
        mock_session = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = payment
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_check.return_value = {
            "id": "ext-pending-1",
            "status": "succeeded",
            "amount": 99.0,
            "currency": "RUB",
        }

        mock_bot = AsyncMock()
        result = await recheck_single_payment("ext-pending-1", mock_bot)

        assert result["updated"] is True
        assert result["status"] == "succeeded"
        assert result["provisioned"] is True
        assert result["error"] is None
        mock_handle.assert_called_once()


@pytest.mark.asyncio
async def test_recheck_single_payment_pending_no_provisioning():
    """YooKassa returns pending → provisioning не вызывается"""
    payment = PaymentModel(
        id=11,
        telegram_user_id=123456789,
        external_id="ext-pending-2",
        amount=99,
        currency="RUB",
        status="pending",
        subscription_id=None,
        created_at=datetime.utcnow(),
    )

    with patch('app.services.payments.recovery.SessionLocal') as mock_sl, \
         patch('app.services.payments.yookassa.check_payment_status') as mock_check, \
         patch('app.services.payments.yookassa.handle_successful_payment') as mock_handle:
        mock_session = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = payment
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_check.return_value = {
            "id": "ext-pending-2",
            "status": "pending",
            "amount": 99.0,
            "currency": "RUB",
        }

        mock_bot = AsyncMock()
        result = await recheck_single_payment("ext-pending-2", mock_bot)

        assert result["updated"] is False
        assert result["status"] == "pending"
        assert result["provisioned"] is False
        mock_handle.assert_not_called()


@pytest.mark.asyncio
async def test_recheck_single_payment_idempotent():
    """Повторное нажатие при succeeded + subscription_id → provisioning не вызывается"""
    payment = PaymentModel(
        id=12,
        telegram_user_id=123456789,
        external_id="ext-done-1",
        amount=99,
        currency="RUB",
        status="succeeded",
        subscription_id=42,
        created_at=datetime.utcnow(),
    )

    with patch('app.services.payments.recovery.SessionLocal') as mock_sl, \
         patch('app.services.payments.yookassa.check_payment_status') as mock_check, \
         patch('app.services.payments.yookassa.handle_successful_payment') as mock_handle:
        mock_session = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = payment
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_bot = AsyncMock()
        result = await recheck_single_payment("ext-done-1", mock_bot)

        assert result["updated"] is False
        assert result["status"] == "succeeded"
        assert result["provisioned"] is True
        mock_check.assert_not_called()
        mock_handle.assert_not_called()


@pytest.mark.asyncio
async def test_recheck_single_payment_not_found():
    """Платёж не найден в БД"""
    with patch('app.services.payments.recovery.SessionLocal') as mock_sl, \
         patch('app.services.payments.yookassa.check_payment_status') as mock_check, \
         patch('app.services.payments.yookassa.handle_successful_payment') as mock_handle:
        mock_session = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_bot = AsyncMock()
        result = await recheck_single_payment("ext-nonexistent", mock_bot)

        assert result["error"] == "not_found"
        assert result["provisioned"] is False
        mock_check.assert_not_called()
        mock_handle.assert_not_called()


@pytest.mark.asyncio
async def test_recheck_single_payment_yookassa_not_found():
    """YooKassa вернула not_found — не меняем статус в БД, возвращаем error=not_found"""
    payment = PaymentModel(
        id=13,
        telegram_user_id=123456789,
        external_id="ext-yk-notfound",
        amount=99,
        currency="RUB",
        status="pending",
        subscription_id=None,
        created_at=datetime.utcnow(),
    )

    with patch('app.services.payments.recovery.SessionLocal') as mock_sl, \
         patch('app.services.payments.yookassa.check_payment_status') as mock_check, \
         patch('app.services.payments.yookassa.handle_successful_payment') as mock_handle:
        mock_session = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = payment
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_check.return_value = {"error": "not_found"}

        mock_bot = AsyncMock()
        result = await recheck_single_payment("ext-yk-notfound", mock_bot)

        assert result["error"] == "not_found"
        assert result["provisioned"] is False
        assert result["updated"] is False
        mock_handle.assert_not_called()
