"""Тесты для промокода Solokhin — заявка админу, Premium 1 месяц"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram import types


@pytest.fixture
def mock_message():
    """Мок сообщения Telegram"""
    user = types.User(
        id=12345,
        is_bot=False,
        first_name="Test",
        last_name="User",
        username="testuser"
    )
    message = MagicMock(spec=types.Message)
    message.from_user = user
    message.text = "/solokhin"
    message.answer = AsyncMock()
    message.bot = MagicMock()
    message.bot.send_message = AsyncMock()
    return message


@pytest.fixture
def mock_sync_result_no_subscription():
    """SyncResult без активной подписки"""
    result = MagicMock()
    result.subscription_status = "inactive"
    return result


@pytest.fixture
def mock_sync_result_active():
    """SyncResult с активной подпиской"""
    result = MagicMock()
    result.subscription_status = "active"
    return result


@pytest.mark.asyncio
async def test_solokhin_sends_request_to_admin(mock_message, mock_sync_result_no_subscription):
    """Промокод Solokhin отправляет заявку админу"""
    from app.routers.start import _handle_solokhin_promo

    with patch("app.routers.start.SyncService") as mock_sync_cls, \
         patch("app.services.payment_request.generate_req_id", return_value="test_req_123"), \
         patch("app.services.payment_request.build_payreq_block", return_value="PAYREQ_BLOCK"), \
         patch("app.services.jsonl_logger.log_payment_event"), \
         patch("app.routers.start.settings") as mock_settings:

        mock_settings.ADMINS = [99999]
        mock_sync = MagicMock()
        mock_sync.sync_user_and_subscription = AsyncMock(return_value=mock_sync_result_no_subscription)
        mock_sync_cls.return_value = mock_sync

        result = await _handle_solokhin_promo(mock_message)

    assert result is True
    mock_message.answer.assert_called_once()
    text = mock_message.answer.call_args[0][0]
    assert "Заявка" in text or "отправлена" in text
    mock_message.bot.send_message.assert_called()  # Админу


@pytest.mark.asyncio
async def test_solokhin_rejects_active_subscription(mock_message, mock_sync_result_active):
    """Промокод /solokhin отклоняется если есть активная подписка"""
    from app.routers.start import _handle_solokhin_promo

    with patch("app.routers.start.SyncService") as mock_sync_cls, \
         patch("app.routers.start.settings") as mock_settings:

        mock_settings.ADMINS = []
        mock_sync = MagicMock()
        mock_sync.sync_user_and_subscription = AsyncMock(return_value=mock_sync_result_active)
        mock_sync_cls.return_value = mock_sync

        result = await _handle_solokhin_promo(mock_message)

    assert result is True  # Обработано, но не выдано
    mock_message.answer.assert_called_once()
    text = mock_message.answer.call_args[0][0]
    assert "активная подписка" in text.lower() or "уже есть" in text.lower()
