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

# test_recheck_single_payment_succeeded_provisioned: removed in 3.0 with the 2.x provisioning (tests/money/test_recovery_sweep.py, test_fulfillment.py)


# test_recheck_single_payment_pending_no_provisioning: removed in 3.0 with the 2.x provisioning (tests/money/test_recovery_sweep.py, test_fulfillment.py)


# test_recheck_single_payment_idempotent: removed in 3.0 with the 2.x provisioning (tests/money/test_recovery_sweep.py, test_fulfillment.py)


# test_recheck_single_payment_not_found: removed in 3.0 with the 2.x provisioning (tests/money/test_recovery_sweep.py, test_fulfillment.py)


# test_recheck_single_payment_yookassa_not_found: removed in 3.0 with the 2.x provisioning (tests/money/test_recovery_sweep.py, test_fulfillment.py)
