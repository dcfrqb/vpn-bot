"""
Интеграционные тесты промокодных команд /solokhin и /trial.
Требуют полного окружения (dateutil, SQLAlchemy и др.) — запускаются в Docker.

Запуск: pytest tests/test_promo_commands_full.py -v

Заметки по патчингу:
- SyncService, settings — импортированы на уровне модуля, патчим в app.routers.start
- SessionLocal, provision_tariff — lazy-import внутри функции, патчим в их источнике
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_message(text="/solokhin", user_id=12345):
    from aiogram import types
    user = types.User(
        id=user_id,
        is_bot=False,
        first_name="Test",
        last_name="User",
        username="testuser",
    )
    msg = MagicMock()
    msg.from_user = user
    msg.text = text
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.send_message = AsyncMock()
    return msg


def _sync_inactive():
    r = MagicMock()
    r.subscription_status = "inactive"
    return r


def _sync_active():
    r = MagicMock()
    r.subscription_status = "active"
    return r


def _no_payment_session():
    """DB session that returns no existing payment record."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


def _existing_payment_session():
    """DB session that returns an existing payment record (already used)."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=MagicMock())
    session.execute = AsyncMock(return_value=result)
    return session


def _patches_happy(promo_code, days, plan_label, admin_ids=None):
    """Context-manager stack for a successful promo grant path."""
    return (
        patch("app.routers.start.SyncService"),
        patch("app.routers.start.settings"),
        patch("app.db.session.SessionLocal", return_value=_no_payment_session()),
        patch("app.services.remna_service.provision_tariff", new_callable=AsyncMock, return_value=True),
        patch("app.services.payment_request.generate_req_id", return_value=f"req_{promo_code}"),
        patch("app.services.jsonl_logger.log_payment_event"),
    )


# ---------------------------------------------------------------------------
# active subscription → reject
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_active_subscription_rejects_solokhin():
    from app.routers.start import _handle_promo_command
    msg = _make_message("/solokhin")
    with patch("app.routers.start.SyncService") as MockSync, \
         patch("app.routers.start.settings") as mock_settings:
        mock_settings.ADMINS = []
        sync_inst = MagicMock()
        sync_inst.sync_user_and_subscription = AsyncMock(return_value=_sync_active())
        MockSync.return_value = sync_inst
        result = await _handle_promo_command(
            msg, promo_code="solokhin", tariff="solokhin_15d", days=15, plan_label="Premium"
        )
    assert result is True
    msg.answer.assert_called_once()
    assert "активная подписка" in msg.answer.call_args[0][0].lower() or \
           "уже есть" in msg.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_active_subscription_rejects_trial():
    from app.routers.start import _handle_promo_command
    msg = _make_message("/trial")
    with patch("app.routers.start.SyncService") as MockSync, \
         patch("app.routers.start.settings") as mock_settings:
        mock_settings.ADMINS = []
        sync_inst = MagicMock()
        sync_inst.sync_user_and_subscription = AsyncMock(return_value=_sync_active())
        MockSync.return_value = sync_inst
        result = await _handle_promo_command(
            msg, promo_code="trial", tariff="trial_10d", days=10, plan_label="Basic"
        )
    assert result is True
    msg.answer.assert_called_once()
    assert "активная подписка" in msg.answer.call_args[0][0].lower() or \
           "уже есть" in msg.answer.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# already used → reject
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_already_used_rejects_solokhin():
    from app.routers.start import _handle_promo_command
    msg = _make_message("/solokhin")
    with patch("app.routers.start.SyncService") as MockSync, \
         patch("app.routers.start.settings") as mock_settings, \
         patch("app.db.session.SessionLocal", return_value=_existing_payment_session()):
        mock_settings.ADMINS = []
        sync_inst = MagicMock()
        sync_inst.sync_user_and_subscription = AsyncMock(return_value=_sync_inactive())
        MockSync.return_value = sync_inst
        result = await _handle_promo_command(
            msg, promo_code="solokhin", tariff="solokhin_15d", days=15, plan_label="Premium"
        )
    assert result is True
    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0]
    assert "уже был использован" in text.lower() or "повторная" in text.lower()


@pytest.mark.asyncio
async def test_already_used_rejects_trial():
    from app.routers.start import _handle_promo_command
    msg = _make_message("/trial")
    with patch("app.routers.start.SyncService") as MockSync, \
         patch("app.routers.start.settings") as mock_settings, \
         patch("app.db.session.SessionLocal", return_value=_existing_payment_session()):
        mock_settings.ADMINS = []
        sync_inst = MagicMock()
        sync_inst.sync_user_and_subscription = AsyncMock(return_value=_sync_inactive())
        MockSync.return_value = sync_inst
        result = await _handle_promo_command(
            msg, promo_code="trial", tariff="trial_10d", days=10, plan_label="Basic"
        )
    assert result is True
    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0]
    assert "уже был использован" in text.lower() or "повторная" in text.lower()


# ---------------------------------------------------------------------------
# happy path: provisioned + user msg + admin notification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_solokhin_grants_premium_15d():
    from app.routers.start import _handle_promo_command
    msg = _make_message("/solokhin")
    with patch("app.routers.start.SyncService") as MockSync, \
         patch("app.routers.start.settings") as mock_settings, \
         patch("app.db.session.SessionLocal", return_value=_no_payment_session()), \
         patch("app.services.remna_service.provision_tariff", new_callable=AsyncMock, return_value=True), \
         patch("app.services.payment_request.generate_req_id", return_value="req_sol"), \
         patch("app.services.jsonl_logger.log_payment_event"):
        mock_settings.ADMINS = [99999]
        sync_inst = MagicMock()
        sync_inst.sync_user_and_subscription = AsyncMock(return_value=_sync_inactive())
        MockSync.return_value = sync_inst
        result = await _handle_promo_command(
            msg, promo_code="solokhin", tariff="solokhin_15d", days=15, plan_label="Premium"
        )
    assert result is True
    msg.answer.assert_called_once()
    user_text = msg.answer.call_args[0][0]
    assert "Premium" in user_text and "15" in user_text
    msg.bot.send_message.assert_called_once()
    admin_text = msg.bot.send_message.call_args[1]["text"]
    assert "SOLOKHIN" in admin_text and "Premium" in admin_text and "15" in admin_text


@pytest.mark.asyncio
async def test_trial_grants_basic_10d():
    from app.routers.start import _handle_promo_command
    msg = _make_message("/trial")
    with patch("app.routers.start.SyncService") as MockSync, \
         patch("app.routers.start.settings") as mock_settings, \
         patch("app.db.session.SessionLocal", return_value=_no_payment_session()), \
         patch("app.services.remna_service.provision_tariff", new_callable=AsyncMock, return_value=True), \
         patch("app.services.payment_request.generate_req_id", return_value="req_trial"), \
         patch("app.services.jsonl_logger.log_payment_event"):
        mock_settings.ADMINS = [99999]
        sync_inst = MagicMock()
        sync_inst.sync_user_and_subscription = AsyncMock(return_value=_sync_inactive())
        MockSync.return_value = sync_inst
        result = await _handle_promo_command(
            msg, promo_code="trial", tariff="trial_10d", days=10, plan_label="Basic"
        )
    assert result is True
    msg.answer.assert_called_once()
    user_text = msg.answer.call_args[0][0]
    assert "Basic" in user_text and "10" in user_text
    msg.bot.send_message.assert_called_once()
    admin_text = msg.bot.send_message.call_args[1]["text"]
    assert "TRIAL" in admin_text and "Basic" in admin_text and "10" in admin_text


# ---------------------------------------------------------------------------
# user messages differ between /solokhin and /trial
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_messages_differ():
    from app.routers.start import _handle_promo_command

    async def run(promo_code, tariff, days, plan_label):
        msg = _make_message(f"/{promo_code}")
        with patch("app.routers.start.SyncService") as MockSync, \
             patch("app.routers.start.settings") as mock_settings, \
             patch("app.db.session.SessionLocal", return_value=_no_payment_session()), \
             patch("app.services.remna_service.provision_tariff", new_callable=AsyncMock, return_value=True), \
             patch("app.services.payment_request.generate_req_id", return_value="req_x"), \
             patch("app.services.jsonl_logger.log_payment_event"):
            mock_settings.ADMINS = []
            sync_inst = MagicMock()
            sync_inst.sync_user_and_subscription = AsyncMock(return_value=_sync_inactive())
            MockSync.return_value = sync_inst
            await _handle_promo_command(msg, promo_code=promo_code, tariff=tariff, days=days, plan_label=plan_label)
        return msg.answer.call_args[0][0]

    sol_text = await run("solokhin", "solokhin_15d", 15, "Premium")
    trial_text = await run("trial", "trial_10d", 10, "Basic")
    assert sol_text != trial_text
    assert "Premium" in sol_text and "15" in sol_text
    assert "Basic" in trial_text and "10" in trial_text


# ---------------------------------------------------------------------------
# provision_tariff called with correct tariff string
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_solokhin_uses_correct_tariff():
    from app.routers.start import _handle_promo_command
    msg = _make_message("/solokhin")
    with patch("app.routers.start.SyncService") as MockSync, \
         patch("app.routers.start.settings") as mock_settings, \
         patch("app.db.session.SessionLocal", return_value=_no_payment_session()), \
         patch("app.services.remna_service.provision_tariff", new_callable=AsyncMock, return_value=True) as mock_prov, \
         patch("app.services.payment_request.generate_req_id", return_value="req_sol2"), \
         patch("app.services.jsonl_logger.log_payment_event"):
        mock_settings.ADMINS = []
        sync_inst = MagicMock()
        sync_inst.sync_user_and_subscription = AsyncMock(return_value=_sync_inactive())
        MockSync.return_value = sync_inst
        await _handle_promo_command(msg, promo_code="solokhin", tariff="solokhin_15d", days=15, plan_label="Premium")
    mock_prov.assert_called_once()
    assert mock_prov.call_args[0][1] == "solokhin_15d"


@pytest.mark.asyncio
async def test_trial_uses_correct_tariff():
    from app.routers.start import _handle_promo_command
    msg = _make_message("/trial")
    with patch("app.routers.start.SyncService") as MockSync, \
         patch("app.routers.start.settings") as mock_settings, \
         patch("app.db.session.SessionLocal", return_value=_no_payment_session()), \
         patch("app.services.remna_service.provision_tariff", new_callable=AsyncMock, return_value=True) as mock_prov, \
         patch("app.services.payment_request.generate_req_id", return_value="req_trial2"), \
         patch("app.services.jsonl_logger.log_payment_event"):
        mock_settings.ADMINS = []
        sync_inst = MagicMock()
        sync_inst.sync_user_and_subscription = AsyncMock(return_value=_sync_inactive())
        MockSync.return_value = sync_inst
        await _handle_promo_command(msg, promo_code="trial", tariff="trial_10d", days=10, plan_label="Basic")
    mock_prov.assert_called_once()
    assert mock_prov.call_args[0][1] == "trial_10d"
