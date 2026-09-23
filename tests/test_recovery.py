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


# test_retry_needs_provisioning_with_flag: 3.0 recovery goes through Fulfillment, see tests/money/test_recovery_sweep.py

# test_retry_needs_provisioning_fallback_old_payment: 3.0 recovery goes through Fulfillment, see tests/money/test_recovery_sweep.py

# test_retry_needs_provisioning_skips_recent_without_flag: 3.0 recovery goes through Fulfillment, see tests/money/test_recovery_sweep.py

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
