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
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, Mock

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


@pytest.mark.asyncio
async def test_phase_b_silent_failure_raises_pending():
    """get_or_create вернул None URL → mark failed + raise ProvisioningPendingError"""
    from app.services.payments.yookassa import handle_successful_payment

    mock_session, state, user, payment_db = _build_session_with_state()
    user.remna_user_id = None  # симулируем silent failure: после get_or_create remna_user_id всё ещё пустой

    with patch('app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url',
               new_callable=AsyncMock,
               return_value=None):
        mock_bot = AsyncMock()
        with pytest.raises(ProvisioningPendingError):
            await handle_successful_payment(
                session=mock_session,
                payment_id=1,
                telegram_user_id=123456789,
                amount=99.0,
                description="CRS VPN",
                bot=mock_bot,
            )

    assert state["subscription"] is not None
    assert state["subscription"].provisioning_state == "failed"
    assert state["subscription"].last_provisioning_error is not None
    _assert_user_not_notified_admin_alerted(mock_bot)


@pytest.mark.asyncio
async def test_phase_b_verify_mismatch_raises_pending():
    """_verify_remnawave_synced вернул ok=False → mark failed + raise"""
    from app.services.payments.yookassa import handle_successful_payment

    mock_session, state, user, payment_db = _build_session_with_state()

    with patch('app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url',
               new_callable=AsyncMock,
               return_value="https://sub.example.com/abc"), \
         patch('app.services.payments.yookassa._verify_remnawave_synced',
               new_callable=AsyncMock,
               return_value=(False, None, "expireAt mismatch")):
        mock_bot = AsyncMock()
        with pytest.raises(ProvisioningPendingError):
            await handle_successful_payment(
                session=mock_session,
                payment_id=1,
                telegram_user_id=123456789,
                amount=99.0,
                description="CRS VPN",
                bot=mock_bot,
            )

    assert state["subscription"].provisioning_state == "failed"
    assert "verification" in (state["subscription"].last_provisioning_error or "")
    _assert_user_not_notified_admin_alerted(mock_bot)


@pytest.mark.asyncio
async def test_phase_b_remna_exception_raises_pending():
    """get_or_create бросил exception → mark failed + raise"""
    from app.services.payments.yookassa import handle_successful_payment

    mock_session, state, user, payment_db = _build_session_with_state()

    with patch('app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url',
               new_callable=AsyncMock,
               side_effect=ConnectionError("Remnawave unreachable")):
        mock_bot = AsyncMock()
        with pytest.raises(ProvisioningPendingError):
            await handle_successful_payment(
                session=mock_session,
                payment_id=1,
                telegram_user_id=123456789,
                amount=99.0,
                description="CRS VPN",
                bot=mock_bot,
            )

    assert state["subscription"].provisioning_state == "failed"
    _assert_user_not_notified_admin_alerted(mock_bot)


@pytest.mark.asyncio
async def test_phase_c_marks_synced_and_notifies():
    """Phase B ok → Phase C ставит provisioning_state='synced', выставляет active=True, valid_until, payment.subscription_id, шлёт уведомление"""
    from app.services.payments.yookassa import handle_successful_payment

    mock_session, state, user, payment_db = _build_session_with_state()

    with patch('app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url',
               new_callable=AsyncMock,
               return_value="https://sub.example.com/abc"), \
         patch('app.services.payments.yookassa._verify_remnawave_synced',
               new_callable=AsyncMock,
               return_value=(True, datetime.utcnow(), None)):
        mock_bot = AsyncMock()
        await handle_successful_payment(
            session=mock_session,
            payment_id=1,
            telegram_user_id=123456789,
            amount=99.0,
            description="CRS VPN",
            bot=mock_bot,
        )

    sub = state["subscription"]
    assert sub.provisioning_state == "synced"
    assert sub.active is True
    assert sub.valid_until is not None
    assert sub.remnawave_synced_at is not None
    assert sub.last_provisioning_error is None
    assert payment_db.subscription_id == sub.id
    mock_bot.send_message.assert_called()


@pytest.mark.asyncio
async def test_resync_subscription_to_remnawave_success():
    """resync для failed подписки → возвращает True, ставит synced"""
    from app.services.payments.yookassa import resync_subscription_to_remnawave

    sub = Subscription(
        id=42,
        telegram_user_id=123456789,
        plan_code="basic",
        active=True,
        valid_until=datetime.utcnow() + timedelta(days=30),
        provisioning_state="failed",
        remna_user_id="ru1",
    )
    user = TelegramUser(telegram_id=123456789, remna_user_id="ru1")

    mock_sub_result = MagicMock()
    mock_sub_result.scalar_one_or_none.return_value = sub
    mock_user_result = MagicMock()
    mock_user_result.scalar_one_or_none.return_value = user

    async def mock_execute(query):
        s = str(query).lower()
        if "from subscriptions" in s:
            return mock_sub_result
        return mock_user_result

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=mock_execute)

    fake_session_local = MagicMock()
    fake_session_local.return_value.__aenter__.return_value = mock_session
    fake_session_local.return_value.__aexit__.return_value = None

    with patch('app.services.payments.yookassa.SessionLocal', fake_session_local), \
         patch('app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url',
               new_callable=AsyncMock, return_value="https://x"), \
         patch('app.services.payments.yookassa._verify_remnawave_synced',
               new_callable=AsyncMock, return_value=(True, sub.valid_until, None)):
        ok = await resync_subscription_to_remnawave(42)

    assert ok is True
    assert sub.provisioning_state == "synced"
    assert sub.remnawave_synced_at is not None


@pytest.mark.asyncio
async def test_resync_subscription_skips_inactive():
    """resync для не-active sub возвращает False, ничего не меняет"""
    from app.services.payments.yookassa import resync_subscription_to_remnawave

    sub = Subscription(
        id=42,
        telegram_user_id=123456789,
        plan_code="basic",
        active=False,
        is_lifetime=False,
        provisioning_state="failed",
    )
    mock_sub_result = MagicMock()
    mock_sub_result.scalar_one_or_none.return_value = sub
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_sub_result)
    fake_session_local = MagicMock()
    fake_session_local.return_value.__aenter__.return_value = mock_session
    fake_session_local.return_value.__aexit__.return_value = None

    with patch('app.services.payments.yookassa.SessionLocal', fake_session_local):
        ok = await resync_subscription_to_remnawave(42)
    assert ok is False
    assert sub.provisioning_state == "failed"  # не изменилось


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
         patch('app.services.payments.yookassa.process_payment_webhook',
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


@pytest.mark.asyncio
async def test_subscription_extension_keeps_old_active_during_phase_b():
    """Existing active sub: Phase A не сбрасывает active/valid_until до подтверждения"""
    from app.services.payments.yookassa import handle_successful_payment

    old_expire = datetime.utcnow() + timedelta(days=10)
    existing_sub = Subscription(
        id=99,
        telegram_user_id=123456789,
        plan_code="basic",
        active=True,
        valid_until=old_expire,
        provisioning_state="synced",
        remna_user_id="ru1",
    )
    user = TelegramUser(telegram_id=123456789, remna_user_id="ru1")
    payment_db = PaymentModel(
        id=1,
        telegram_user_id=123456789,
        external_id="ext-1",
        amount=99.0,
        currency="RUB",
        status="succeeded",
        payment_metadata={},
    )

    mock_user_result = MagicMock()
    mock_user_result.scalar_one_or_none.return_value = user
    mock_sub_result = MagicMock()
    mock_sub_result.scalar_one_or_none.return_value = existing_sub
    mock_payment_result = MagicMock()
    mock_payment_result.scalar_one_or_none.return_value = payment_db

    async def mock_execute(query):
        s = str(query).lower()
        if "from subscriptions" in s:
            return mock_sub_result
        if "from telegram_users" in s:
            return mock_user_result
        return mock_payment_result

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=mock_execute)

    # Phase B fails → Phase C never runs → existing_sub.valid_until должен остаться old_expire
    with patch('app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url',
               new_callable=AsyncMock,
               side_effect=ConnectionError("timeout")):
        mock_bot = AsyncMock()
        with pytest.raises(ProvisioningPendingError):
            await handle_successful_payment(
                session=mock_session,
                payment_id=1,
                telegram_user_id=123456789,
                amount=99.0,
                description="CRS VPN",
                bot=mock_bot,
            )

    # Subscription осталась active (не сбросилась) — для extension важно, чтобы юзер
    # не потерял доступ во время сбоя Remnawave. valid_until тоже не обновлено
    # (новое значение пишется только после verify ok).
    assert existing_sub.active is True
    assert existing_sub.valid_until == old_expire
    assert existing_sub.provisioning_state == "failed"
    assert existing_sub.remnawave_expected_expire_at is not None
    assert existing_sub.remnawave_expected_expire_at != old_expire
