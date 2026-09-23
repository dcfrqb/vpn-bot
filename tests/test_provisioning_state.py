"""Тесты для нового provisioning state flow.

Покрывает:
- Phase B failure → ProvisioningPendingError, provisioning_state='failed', юзеру ничего, админу один алерт
- Phase B verify mismatch → ProvisioningPendingError
- Idempotent skip только при provisioning_state='synced'
- resync_subscription_to_remnawave: успех / провал / inactive sub
- Reconciler: shallow scan находит pending/failed и синкает
- Reconciler: deep scan детектит desync, помечает failed
- Webhook → 503 при ProvisioningPendingError
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.payments.errors import ProvisioningPendingError
from app.db.models import Payment as PaymentModel, Subscription, TelegramUser

TEST_ADMIN_ID = 900000099


@pytest.fixture(autouse=True)
def _isolated_admins_and_redis():
    """Не зависим от локального .env (ADMINS) и живого Redis на localhost."""
    from app.services.payments import yookassa as yk
    with patch.object(yk.settings, "ADMINS", [TEST_ADMIN_ID]), \
         patch("app.services.cache.get_redis_client", return_value=None):
        yield


def _assert_user_not_notified_admin_alerted(mock_bot, tg_id=123456789):
    """Юзеру ничего (доступа нет), админу ровно один алерт «оплата есть, доступ не выдан»."""
    chats = [c.kwargs.get("chat_id") for c in mock_bot.send_message.await_args_list]
    assert tg_id not in chats
    assert chats == [TEST_ADMIN_ID]
    assert "доступ не выдан" in mock_bot.send_message.await_args_list[0].kwargs["text"]


def _build_session_with_state():
    """Готовит mock_session, отслеживающий subscription через session.add."""
    user = TelegramUser(
        telegram_id=123456789,
        username="test_user",
        remna_user_id="test-remna-uuid",
    )
    payment_db = PaymentModel(
        id=1,
        telegram_user_id=123456789,
        external_id="ext-1",
        amount=99.0,
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
        # FROM clause различает таблицы; проверяем именно её, иначе SQL по
        # таблице payments (содержащий subscription_id) ложно матчится.
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
    return mock_session, state, user, payment_db


# test_phase_b_silent_failure_raises_pending: removed in 3.0 with the 2.x provisioning (tests/panel (grant phases) and tests/money)


# test_phase_b_verify_mismatch_raises_pending: removed in 3.0 with the 2.x provisioning (tests/panel (grant phases) and tests/money)


# test_phase_b_remna_exception_raises_pending: removed in 3.0 with the 2.x provisioning (tests/panel (grant phases) and tests/money)


# test_phase_c_marks_synced_and_notifies: removed in 3.0 with the 2.x provisioning (tests/panel (grant phases) and tests/money)


# test_resync_subscription_to_remnawave_success: removed in 3.0 with the 2.x provisioning (tests/panel (grant phases) and tests/money)


# test_resync_subscription_skips_inactive: removed in 3.0 with the 2.x provisioning (tests/panel (grant phases) and tests/money)


@pytest.mark.asyncio
async def test_reconciler_shallow_finds_and_resyncs():
    """Reconciler.shallow_scan находит pending/failed sub и вызывает resync"""
    from app.tasks.remnawave_reconciler import RemnawaveReconciler

    sub = Subscription(
        id=42,
        telegram_user_id=123456789,
        plan_code="basic",
        active=True,
        valid_until=datetime.utcnow() + timedelta(days=30),
        provisioning_state="failed",
        remna_user_id="ru1",
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [sub]
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    fake_session_local = MagicMock()
    fake_session_local.return_value.__aenter__.return_value = mock_session
    fake_session_local.return_value.__aexit__.return_value = None

    bot = AsyncMock()
    rec = RemnawaveReconciler(bot)

    with patch('app.tasks.remnawave_reconciler.SessionLocal', fake_session_local), \
         patch('app.services.payments.yookassa.resync_subscription_to_remnawave',
               new_callable=AsyncMock, return_value=True) as mock_resync, \
         patch('app.services.cache.acquire_provision_lock',
               new_callable=AsyncMock, return_value=True), \
         patch('app.services.cache.release_provision_lock',
               new_callable=AsyncMock):
        out = await rec._shallow_scan()

    assert out["shallow_found"] == 1
    assert out["shallow_synced"] == 1
    assert out["shallow_failed"] == 0
    mock_resync.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_reconciler_deep_scan_marks_desync_failed():
    """Deep scan: synced sub с фактическим Remnawave EXPIRED → помечает failed"""
    from app.tasks.remnawave_reconciler import RemnawaveReconciler

    sub = Subscription(
        id=42,
        telegram_user_id=123456789,
        plan_code="basic",
        active=True,
        valid_until=datetime.utcnow() + timedelta(days=30),
        provisioning_state="synced",
        remna_user_id="ru1",
    )

    # Two separate session contexts in deep_scan: одна для select, одна для UPDATE
    mock_select_result = MagicMock()
    mock_select_result.scalars.return_value.all.return_value = [sub]
    mock_select_session = AsyncMock()
    mock_select_session.execute = AsyncMock(return_value=mock_select_result)

    mock_update_result = MagicMock()
    mock_update_result.scalar_one_or_none.return_value = sub
    mock_update_session = AsyncMock()
    mock_update_session.execute = AsyncMock(return_value=mock_update_result)

    sessions_iter = iter([mock_select_session, mock_update_session])

    fake_session_local = MagicMock()

    def _new_ctx(*a, **kw):
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=next(sessions_iter))
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx

    fake_session_local.side_effect = _new_ctx

    fake_remna = AsyncMock()
    fake_remna.get_user_by_id = AsyncMock(
        return_value={"response": {"status": "EXPIRED", "expireAt": "2020-01-01T00:00:00Z"}}
    )
    fake_remna.close = AsyncMock()

    bot = AsyncMock()
    rec = RemnawaveReconciler(bot)

    with patch('app.tasks.remnawave_reconciler.SessionLocal', fake_session_local), \
         patch('app.tasks.remnawave_reconciler.RemnaClient', return_value=fake_remna):
        out = await rec._deep_scan()

    assert out["deep_scanned"] == 1
    assert out["deep_desynced"] == 1
    assert sub.provisioning_state == "failed"
    assert "deep_scan" in (sub.last_provisioning_error or "")


@pytest.mark.asyncio
async def test_webhook_returns_503_on_provisioning_pending():
    """yookassa_webhook возвращает 503 при ProvisioningPendingError"""
    from fastapi.testclient import TestClient
    from app.api.main import app

    with patch('app.api.routes.yookassa._is_yookassa_ip', return_value=True), \
         patch('app.api.routes.yookassa._webhook_rate_limit_ok', new_callable=AsyncMock, return_value=True), \
         patch('app.services.payments.webhook.process_payment_webhook',
               new_callable=AsyncMock,
               side_effect=ProvisioningPendingError("Remnawave timeout")):
        # bot_instance must be truthy
        with patch('app.api.routes.yookassa.bot_instance', new=AsyncMock()):
            client = TestClient(app)
            r = client.post(
                "/webhook/yookassa",
                json={
                    "event": "payment.succeeded",
                    "object": {"id": "pay-1", "status": "succeeded"},
                },
            )
    assert r.status_code == 503
    body = r.json()
    assert body.get("status") == "retry"


# test_subscription_extension_keeps_old_active_during_phase_b: removed in 3.0 with the 2.x provisioning (tests/panel (grant phases) and tests/money)
