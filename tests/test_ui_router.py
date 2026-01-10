"""
Тесты для единого UI router
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram import types
from app.routers.ui import ui_callback_handler
from app.ui.screens import ScreenID
from app.ui.callbacks import build_cb


@pytest.fixture
def mock_callback():
    """Создает mock CallbackQuery"""
    callback = MagicMock(spec=types.CallbackQuery)
    # Явно создаем from_user как MagicMock
    callback.from_user = MagicMock()
    callback.from_user.id = 12345
    callback.from_user.first_name = "Test"
    callback.from_user.last_name = "User"
    callback.from_user.username = "testuser"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.chat.id = 12345
    callback.message.message_id = 1
    return callback


@pytest.mark.asyncio
async def test_ui_callback_open_connect(mock_callback):
    """Тест: callback ui:CONNECT:open:- приводит к ScreenManager.show_screen(CONNECT)"""
    mock_callback.data = build_cb(ScreenID.CONNECT, "open")
    
    with patch('app.routers.ui.get_screen_manager') as mock_get_manager:
        mock_manager = MagicMock()
        mock_manager.handle_action = AsyncMock(return_value=True)
        mock_get_manager.return_value = mock_manager
        
        await ui_callback_handler(mock_callback)
        
        # Проверяем, что handle_action был вызван
        mock_manager.handle_action.assert_called_once()
        call_args = mock_manager.handle_action.call_args
        assert call_args[1]['screen_id'] == ScreenID.CONNECT
        assert call_args[1]['action'] == "open"
        assert call_args[1]['payload'] == "-"
        
        # Проверяем, что callback.answer был вызван
        mock_callback.answer.assert_called_once()


@pytest.mark.asyncio
async def test_ui_callback_admin_panel_non_admin(mock_callback):
    """Тест: callback ui:ADMIN_PANEL:open:- для не-админа запрещен"""
    mock_callback.data = build_cb(ScreenID.ADMIN_PANEL, "open")
    
    with patch('app.routers.ui.get_screen_manager') as mock_get_manager:
        mock_manager = MagicMock()
        # handle_action возвращает False (запрещен доступ)
        mock_manager.handle_action = AsyncMock(return_value=False)
        mock_get_manager.return_value = mock_manager
        
        await ui_callback_handler(mock_callback)
        
        # Проверяем, что handle_action был вызван
        mock_manager.handle_action.assert_called_once()
        
        # Проверяем, что был показан alert об ошибке
        mock_callback.answer.assert_called()
        # Последний вызов должен быть с show_alert=True
        calls = mock_callback.answer.call_args_list
        assert any("Не удалось выполнить действие" in str(call) for call in calls)


@pytest.mark.asyncio
async def test_ui_callback_invalid_format(mock_callback):
    """Тест: неверный формат callback_data"""
    mock_callback.data = "invalid:format"
    
    with patch('app.routers.ui.parse_cb', return_value=None):
        await ui_callback_handler(mock_callback)
        
        # Должен быть показан alert об ошибке
        mock_callback.answer.assert_called()
        calls = mock_callback.answer.call_args_list
        assert any("Ошибка обработки запроса" in str(call) for call in calls)


@pytest.mark.asyncio
async def test_ui_callback_parse_error(mock_callback):
    """Тест: ошибка парсинга callback_data"""
    from app.ui.callbacks import CallbackParseError
    
    mock_callback.data = "ui:invalid_screen:open:-"
    
    with patch('app.routers.ui.parse_cb', side_effect=CallbackParseError("Неизвестный ScreenID")):
        await ui_callback_handler(mock_callback)
        
        # Должен быть показан alert об ошибке формата
        mock_callback.answer.assert_called()
        calls = mock_callback.answer.call_args_list
        assert any("Ошибка формата запроса" in str(call) for call in calls)