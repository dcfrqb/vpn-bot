"""
Тесты для handlers /start.

Покрывают:
- быстрый ответ
- не падает при отсутствии подписки
- кнопка "Обновить" вызывает force_remna
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
    message.text = "/start"
    message.answer = AsyncMock()
    message.bot = AsyncMock()
    return message


@pytest.fixture
def mock_callback():
    """Мок callback query"""
    user = types.User(
        id=12345,
        is_bot=False,
        first_name="Test",
        last_name="User",
        username="testuser"
    )
    callback = MagicMock(spec=types.CallbackQuery)
    callback.from_user = user
    callback.data = "refresh_info"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.chat.id = 12345
    callback.message.message_id = 1
    callback.bot = AsyncMock()
    callback.bot.edit_message_text = AsyncMock()
    return callback


@pytest.mark.asyncio
async def test_start_handler_fast_response(mock_message):
    """Тест: /start handler быстро отвечает через ScreenManager"""
    from app.routers.start import cmd_start
    from app.ui.screen_manager import ScreenManager
    from app.navigation.navigator import Navigator
    from app.ui.screens import ScreenID
    
    sync_result = SyncResult(
        is_new_user_created=False,
        user_remna_uuid="remna-uuid-123",
        subscription_status="none",
        expires_at=None,
        source="cache"
    )
    
    with patch('app.routers.start.get_navigator') as mock_get_navigator, \
         patch('app.routers.start.get_screen_manager') as mock_get_sm, \
         patch('app.routers.start.SyncService') as mock_sync_service_class, \
         patch('app.routers.start.get_main_menu_viewmodel') as mock_get_vm:
        
        # Мокируем Navigator
        mock_navigator = MagicMock(spec=Navigator)
        mock_navigator.clear_backstack = MagicMock()
        mock_navigator.clear_flow_anchor = MagicMock()
        mock_navigator._set_current_screen = MagicMock()
        mock_navigator.handle = MagicMock(return_value=MagicMock(target_screen=ScreenID.MAIN_MENU))
        mock_get_navigator.return_value = mock_navigator
        
        # Мокируем ScreenManager
        mock_sm = AsyncMock(spec=ScreenManager)
        mock_sm.show_screen = AsyncMock(return_value=True)
        mock_get_sm.return_value = mock_sm
        
        # Мокируем SyncService
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(return_value=sync_result)
        mock_sync_service_class.return_value = mock_sync_service
        
        # Мокируем ViewModel
        from app.ui.viewmodels.main_menu import MainMenuViewModel
        mock_get_vm.return_value = MainMenuViewModel()
        
        # Выполняем handler
        await cmd_start(mock_message)
        
        # Проверяем, что ScreenManager.show_screen был вызван
        mock_sm.show_screen.assert_called_once()
        call_kwargs = mock_sm.show_screen.call_args[1]
        assert call_kwargs['screen_id'] == ScreenID.MAIN_MENU
        assert call_kwargs['edit'] is False


@pytest.mark.asyncio
async def test_start_handler_no_subscription(mock_message):
    """Тест: /start handler не падает при отсутствии подписки"""
    from app.routers.start import cmd_start
    from app.ui.screen_manager import ScreenManager
    from app.navigation.navigator import Navigator
    from app.ui.screens import ScreenID
    
    mock_sync_result = SyncResult(
        is_new_user_created=False,
        user_remna_uuid=None,
        subscription_status="none",
        expires_at=None,
        source="remna"
    )
    
    with patch('app.routers.start.get_navigator') as mock_get_navigator, \
         patch('app.routers.start.get_screen_manager') as mock_get_sm, \
         patch('app.routers.start.SyncService') as mock_sync_service_class, \
         patch('app.routers.start.get_main_menu_viewmodel') as mock_get_vm:
        
        mock_navigator = MagicMock(spec=Navigator)
        mock_navigator.clear_backstack = MagicMock()
        mock_navigator.clear_flow_anchor = MagicMock()
        mock_navigator._set_current_screen = MagicMock()
        mock_navigator.handle = MagicMock(return_value=MagicMock(target_screen=ScreenID.MAIN_MENU))
        mock_get_navigator.return_value = mock_navigator
        
        mock_sm = AsyncMock(spec=ScreenManager)
        mock_sm.show_screen = AsyncMock(return_value=True)
        mock_get_sm.return_value = mock_sm
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(return_value=mock_sync_result)
        mock_sync_service_class.return_value = mock_sync_service
        
        from app.ui.viewmodels.main_menu import MainMenuViewModel
        mock_get_vm.return_value = MainMenuViewModel()
        
        # Выполняем handler - не должно быть исключений
        await cmd_start(mock_message)
        
        # Проверяем, что ScreenManager.show_screen был вызван
        mock_sm.show_screen.assert_called_once()


@pytest.mark.asyncio
async def test_start_handler_with_active_subscription(mock_message):
    """Тест: /start handler с активной подпиской"""
    from app.routers.start import cmd_start
    from app.ui.screen_manager import ScreenManager
    from app.navigation.navigator import Navigator
    from app.ui.screens import ScreenID
    
    expires_at = datetime.utcnow() + timedelta(days=30)
    sync_result = SyncResult(
        is_new_user_created=False,
        user_remna_uuid="remna-uuid-123",
        subscription_status="active",
        expires_at=expires_at,
        source="remna"
    )
    
    with patch('app.routers.start.get_navigator') as mock_get_navigator, \
         patch('app.routers.start.get_screen_manager') as mock_get_sm, \
         patch('app.routers.start.SyncService') as mock_sync_service_class, \
         patch('app.routers.start.get_main_menu_viewmodel') as mock_get_vm:
        
        mock_navigator = MagicMock(spec=Navigator)
        mock_navigator.clear_backstack = MagicMock()
        mock_navigator.clear_flow_anchor = MagicMock()
        mock_navigator._set_current_screen = MagicMock()
        mock_navigator.handle = MagicMock(return_value=MagicMock(target_screen=ScreenID.MAIN_MENU))
        mock_get_navigator.return_value = mock_navigator
        
        mock_sm = AsyncMock(spec=ScreenManager)
        mock_sm.show_screen = AsyncMock(return_value=True)
        mock_get_sm.return_value = mock_sm
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(return_value=sync_result)
        mock_sync_service_class.return_value = mock_sync_service
        
        from app.ui.viewmodels.main_menu import MainMenuViewModel
        mock_get_vm.return_value = MainMenuViewModel()
        
        await cmd_start(mock_message)
        
        # Проверяем, что ScreenManager.show_screen был вызван
        mock_sm.show_screen.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_button_calls_force_remna(mock_callback):
    """Тест: кнопка 'Обновить' вызывает force_remna и использует ScreenManager"""
    from app.routers.start import refresh_info
    from app.ui.screen_manager import ScreenManager
    from app.ui.screens import ScreenID
    
    expires_at = datetime.utcnow() + timedelta(days=30)
    sync_result = SyncResult(
        is_new_user_created=False,
        user_remna_uuid="remna-uuid-123",
        subscription_status="active",
        expires_at=expires_at,
        source="remna"
    )
    
    with patch('app.routers.start.invalidate_sync_cache') as mock_invalidate, \
         patch('app.routers.start.SyncService') as mock_sync_service_class, \
         patch('app.routers.start.get_screen_manager') as mock_get_sm, \
         patch('app.routers.start.get_main_menu_viewmodel') as mock_get_vm:
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(return_value=sync_result)
        mock_sync_service_class.return_value = mock_sync_service
        
        mock_sm = AsyncMock(spec=ScreenManager)
        mock_sm.show_screen = AsyncMock(return_value=True)
        mock_get_sm.return_value = mock_sm
        
        from app.ui.viewmodels.main_menu import MainMenuViewModel
        mock_get_vm.return_value = MainMenuViewModel()
        
        await refresh_info(mock_callback)
        
        # Проверяем, что кэш был инвалидирован
        mock_invalidate.assert_called_once_with(mock_callback.from_user.id)
        
        # Проверяем, что sync был вызван с force_remna=True
        mock_sync_service.sync_user_and_subscription.assert_called_once()
        call_kwargs = mock_sync_service.sync_user_and_subscription.call_args[1]
        assert call_kwargs['force_remna'] is True
        assert call_kwargs['use_cache'] is False
        assert call_kwargs['use_fallback'] is False
        
        # Проверяем, что ScreenManager.show_screen был вызван
        mock_sm.show_screen.assert_called_once()
        call_kwargs = mock_sm.show_screen.call_args[1]
        assert call_kwargs['screen_id'] == ScreenID.MAIN_MENU
        assert call_kwargs['edit'] is True


@pytest.mark.asyncio
async def test_refresh_button_remna_unavailable_shows_error(mock_callback):
    """Тест: кнопка 'Обновить' при недоступности Remna показывает ошибку"""
    from app.routers.start import refresh_info
    
    with patch('app.routers.start.invalidate_sync_cache'), \
         patch('app.routers.start.SyncService') as mock_sync_service_class:
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(
            side_effect=RemnaUnavailableError("Remna API недоступна")
        )
        mock_sync_service_class.return_value = mock_sync_service
        
        await refresh_info(mock_callback)
        
        # Проверяем, что было показано сообщение об ошибке (через edit_text)
        mock_callback.message.edit_text.assert_called_once()
        call_args = mock_callback.message.edit_text.call_args
        assert "Не удалось обновить данные" in call_args[0][0] or "Remna" in call_args[0][0] or "Ошибка" in call_args[0][0]
        
        # Проверяем, что был вызван callback.answer с ошибкой
        mock_callback.answer.assert_called()


@pytest.mark.asyncio
async def test_refresh_button_returns_actual_status(mock_callback):
    """Тест: кнопка 'Обновить' возвращает актуальный статус из Remna через ScreenManager"""
    from app.routers.start import refresh_info
    from app.ui.screen_manager import ScreenManager
    from app.ui.screens import ScreenID
    
    expires_at = datetime.utcnow() + timedelta(days=15)
    sync_result = SyncResult(
        is_new_user_created=False,
        user_remna_uuid="remna-uuid-123",
        subscription_status="active",
        expires_at=expires_at,
        source="remna"
    )
    
    with patch('app.routers.start.invalidate_sync_cache'), \
         patch('app.routers.start.SyncService') as mock_sync_service_class, \
         patch('app.routers.start.get_screen_manager') as mock_get_sm, \
         patch('app.routers.start.get_main_menu_viewmodel') as mock_get_vm:
        
        mock_sync_service = AsyncMock()
        mock_sync_service.sync_user_and_subscription = AsyncMock(return_value=sync_result)
        mock_sync_service_class.return_value = mock_sync_service
        
        mock_sm = AsyncMock(spec=ScreenManager)
        mock_sm.show_screen = AsyncMock(return_value=True)
        mock_get_sm.return_value = mock_sm
        
        from app.ui.viewmodels.main_menu import MainMenuViewModel
        mock_get_vm.return_value = MainMenuViewModel()
        
        await refresh_info(mock_callback)
        
        # Проверяем, что ScreenManager.show_screen был вызван с правильными параметрами
        mock_sm.show_screen.assert_called_once()
        call_kwargs = mock_sm.show_screen.call_args[1]
        assert call_kwargs['screen_id'] == ScreenID.MAIN_MENU
        assert call_kwargs['edit'] is True
