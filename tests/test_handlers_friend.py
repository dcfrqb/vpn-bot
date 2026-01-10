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
    
    with patch('app.routers.start.SyncService') as mock_sync_service_class, \
         patch('app.routers.start.can_create_request', return_value=(True, None)), \
         patch('app.routers.start.message.answer') as mock_answer:
        
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
    
    with patch('app.routers.start.SyncService') as mock_sync_service_class, \
         patch('app.routers.start.can_create_request', return_value=(True, None)):
        
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
async def test_friend_request_yes_with_active_subscription_forbidden(mock_callback):
    """Тест: friend_request_yes при активной подписке -> запрос запрещён"""
    from app.routers.start import friend_request_yes
    
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
        mock_sync_service.sync_user_and_subscription = AsyncMock(
            return_value=sync_result
        )
        mock_sync_service_class.return_value = mock_sync_service
        
        await friend_request_yes(mock_callback)
        
        # Проверяем, что было показано сообщение о запрете
        mock_callback.message.edit_text.assert_called_once()
        call_args = mock_callback.message.edit_text.call_args
        text = call_args[0][0]
        assert "активная подписка" in text.lower() or "не может быть создан" in text.lower()
        
        # Проверяем, что sync был вызван с force_remna=True
        call_kwargs = mock_sync_service.sync_user_and_subscription.call_args[1]
        assert call_kwargs['force_remna'] is True


@pytest.mark.asyncio
async def test_friend_request_yes_without_subscription_allowed(mock_callback):
    """Тест: friend_request_yes без подписки -> запрос разрешён"""
    from app.routers.start import friend_request_yes
    
    sync_result = SyncResult(
        is_new_user_created=False,
        user_remna_uuid="remna-uuid-123",
        subscription_status="none",
        expires_at=None,
        source="remna"
    )
    
    with patch('app.routers.start.SyncService') as mock_sync_service_class, \
         patch('app.routers.start.can_create_request', return_value=(True, None)), \
         patch('app.routers.start.create_access_request') as mock_create_request, \
         patch('app.routers.start.get_admin_access_request_keyboard') as mock_admin_keyboard, \
         patch('app.routers.start.settings') as mock_settings:
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(
            return_value=sync_result
        )
        mock_sync_service_class.return_value = mock_sync_service
        
        # Мок созданного запроса
        from app.db.models import AccessRequest
        mock_request = MagicMock(spec=AccessRequest)
        mock_request.id = 1
        mock_create_request.return_value = mock_request
        
        mock_admin_keyboard.return_value = MagicMock()
        mock_settings.ADMINS = [99999]  # Мок админа
        
        await friend_request_yes(mock_callback)
        
        # Проверяем, что запрос был создан
        mock_create_request.assert_called_once()
        
        # Проверяем, что было отправлено сообщение пользователю
        mock_callback.message.edit_text.assert_called_once()
        call_args = mock_callback.message.edit_text.call_args
        text = call_args[0][0]
        assert "отправлен" in text.lower() or "ожидайте" in text.lower()


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
    
    with patch('app.routers.start.SyncService') as mock_sync_service_class, \
         patch('app.routers.start.can_create_request', return_value=(True, None)):
        
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
