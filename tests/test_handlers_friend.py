"""
Тесты для handlers /friend.

Покрывают:
- /friend всегда выполняет force_remna
- /friend не использует кэш или БД
- подписка есть -> запрос запрещён
- подписки нет -> запрос разрешён
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from aiogram import types

from app.services.sync_service import SyncService, SyncResult, RemnaUnavailableError


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
    message.text = "/friend"
    message.answer = AsyncMock()
    message.bot = AsyncMock()
    return message


@pytest.fixture
def mock_callback():
    """Мок callback query для friend_request_yes"""
    user = types.User(
        id=12345,
        is_bot=False,
        first_name="Test",
        last_name="User",
        username="testuser"
    )
    callback = MagicMock(spec=types.CallbackQuery)
    callback.from_user = user
    callback.data = "friend_request_yes"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.bot = AsyncMock()
    callback.bot.send_message = AsyncMock()
    return callback


@pytest.mark.asyncio
async def test_friend_handler_always_uses_force_remna(mock_message):
    """Тест: /friend всегда выполняет force_remna"""
    from app.routers.start import cmd_friend
    
    expires_at = datetime.utcnow() + timedelta(days=30)
    sync_result = SyncResult(
        is_new_user_created=False,
        user_remna_uuid="remna-uuid-123",
        subscription_status="active",
        expires_at=expires_at,
        source="remna"
    )
    
    with patch('app.routers.start.SyncService') as mock_sync_service_class:
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(return_value=sync_result)
        mock_sync_service_class.return_value = mock_sync_service
        
        await cmd_friend(mock_message)
        
        # Проверяем, что sync был вызван с force_remna=True
        mock_sync_service.sync_user_and_subscription.assert_called_once()
        call_kwargs = mock_sync_service.sync_user_and_subscription.call_args[1]
        assert call_kwargs['force_remna'] is True
        assert call_kwargs['use_cache'] is False
        assert call_kwargs['use_fallback'] is False


@pytest.mark.asyncio
async def test_friend_handler_with_active_subscription_forbidden(mock_message):
    """Тест: /friend при активной подписке -> запрос запрещён"""
    from app.routers.start import cmd_friend
    
    expires_at = datetime.utcnow() + timedelta(days=30)
    sync_result = SyncResult(
        is_new_user_created=False,
        user_remna_uuid="remna-uuid-123",
        subscription_status="active",
        expires_at=expires_at,
        source="remna"
    )
    
    with patch('app.routers.start.SyncService') as mock_sync_service_class:
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(return_value=sync_result)
        mock_sync_service_class.return_value = mock_sync_service
        
        await cmd_friend(mock_message)
        
        # Проверяем, что было отправлено сообщение о запрете
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        text = call_args[0][0]
        assert "активная подписка" in text.lower() or "недоступна" in text.lower() or "не может" in text.lower()


@pytest.mark.asyncio
async def test_friend_handler_without_subscription_allowed(mock_message):
    """Тест: /friend без подписки -> запрос разрешён"""
    from app.routers.start import cmd_friend
    
    sync_result = SyncResult(
        is_new_user_created=False,
        user_remna_uuid="remna-uuid-123",
        subscription_status="none",
        expires_at=None,
        source="remna"
    )
    
    with patch('app.routers.start.SyncService') as mock_sync_service_class:
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(return_value=sync_result)
        mock_sync_service_class.return_value = mock_sync_service
        
        await cmd_friend(mock_message)
        
        # Проверяем, что было показано подтверждение запроса
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        text = call_args[0][0]
        assert "администратор" in text.lower() or "доступ" in text.lower() or "запрос" in text.lower()


@pytest.mark.asyncio
async def test_friend_handler_remna_unavailable_shows_error(mock_message):
    """Тест: /friend при недоступности Remna показывает ошибку"""
    from app.routers.start import cmd_friend
    
    with patch('app.routers.start.SyncService') as mock_sync_service_class:
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(
            side_effect=RemnaUnavailableError("Remna API недоступна")
        )
        mock_sync_service_class.return_value = mock_sync_service
        
        await cmd_friend(mock_message)
        
        # Проверяем, что было показано сообщение об ошибке
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        text = call_args[0][0]
        assert "не удалось" in text.lower() or "попробуйте позже" in text.lower() or "ошибка" in text.lower()


@pytest.mark.asyncio
async def test_friend_handler_does_not_use_cache(mock_message):
    """Тест: /friend НЕ использует кэш"""
    from app.routers.start import cmd_friend
    
    sync_result = SyncResult(
        is_new_user_created=False,
        user_remna_uuid="remna-uuid-123",
        subscription_status="none",
        expires_at=None,
        source="remna"
    )
    
    with patch('app.routers.start.SyncService') as mock_sync_service_class:
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(return_value=sync_result)
        mock_sync_service_class.return_value = mock_sync_service
        
        await cmd_friend(mock_message)
        
        # Проверяем, что sync был вызван с use_cache=False
        mock_sync_service.sync_user_and_subscription.assert_called_once()
        call_kwargs = mock_sync_service.sync_user_and_subscription.call_args[1]
        assert call_kwargs['use_cache'] is False


@pytest.mark.asyncio
async def test_friend_handler_does_not_use_fallback(mock_message):
    """Тест: /friend НЕ использует fallback из БД"""
    from app.routers.start import cmd_friend
    
    with patch('app.routers.start.SyncService') as mock_sync_service_class:
        
        mock_sync_service = AsyncMock()
        # Remna недоступна
        mock_sync_service.sync_user_and_subscription = AsyncMock(
            side_effect=RemnaUnavailableError("Remna API недоступна")
        )
        mock_sync_service_class.return_value = mock_sync_service
        
        await cmd_friend(mock_message)
        
        # Проверяем, что sync был вызван с use_fallback=False
        call_kwargs = mock_sync_service.sync_user_and_subscription.call_args[1]
        assert call_kwargs['use_fallback'] is False
        
        # Проверяем, что было показано сообщение об ошибке (не fallback данные)
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        text = call_args[0][0]
        assert "не удалось" in text.lower() or "попробуйте позже" in text.lower() or "ошибка" in text.lower()


# Удалены тесты friend_request_yes: кнопка давно заглушка («Используйте /friend»),
# модуля app.services.access_request больше нет (хотфикс 2.1, чистка по 08 §3c).
