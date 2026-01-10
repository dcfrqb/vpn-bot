"""
Тест логики показа экрана помощи из разных контекстов
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram import types
from app.ui.screen_manager import ScreenManager
from app.ui.screens import ScreenID
from app.ui.viewmodels.base import BaseViewModel


@pytest.fixture
def mock_callback():
    """Создает мок CallbackQuery"""
    callback = MagicMock(spec=types.CallbackQuery)
    # Создаем from_user как MagicMock с атрибутами
    callback.from_user = MagicMock()
    callback.from_user.id = 12345
    callback.from_user.first_name = "Test"
    callback.from_user.last_name = "User"
    callback.from_user.username = "testuser"
    callback.data = "ui:help:open:-"
    callback.message = MagicMock(spec=types.Message)
    callback.message.chat.id = 12345
    callback.message.message_id = 100
    callback.message.edit_text = AsyncMock()
    callback.message.delete = AsyncMock()
    callback.answer = AsyncMock()
    callback.bot = MagicMock()
    callback.bot.send_message = AsyncMock()
    return callback


@pytest.fixture
def screen_manager():
    """Создает экземпляр ScreenManager"""
    return ScreenManager()


@pytest.mark.asyncio
async def test_help_screen_from_payment_screen(mock_callback, screen_manager):
    """
    Тест: открытие экрана помощи из экрана оплаты
    Ожидается: сообщение редактируется, показывается экран помощи
    """
    # Устанавливаем текущий экран как SUBSCRIPTION_PAYMENT
    screen_manager._set_current_screen(mock_callback.from_user.id, ScreenID.SUBSCRIPTION_PAYMENT)
    
    # Мокаем создание ViewModel
    with patch.object(screen_manager, '_create_viewmodel_for_screen') as mock_create_vm:
        mock_viewmodel = MagicMock(spec=BaseViewModel)
        mock_viewmodel.screen_id = ScreenID.HELP
        mock_create_vm.return_value = mock_viewmodel
        
        # Мокаем render и build_keyboard
        with patch('app.ui.screens.help.HelpScreen.render', new_callable=AsyncMock) as mock_render:
            mock_render.return_value = "ℹ️ Справка по CRS-VPN\n\n..."
            
            with patch('app.ui.screens.help.HelpScreen.build_keyboard', new_callable=AsyncMock) as mock_keyboard:
                from aiogram.types import InlineKeyboardMarkup
                mock_keyboard.return_value = InlineKeyboardMarkup(inline_keyboard=[])
                
                # Вызываем handle_action
                result = await screen_manager.handle_action(
                    screen_id=ScreenID.HELP,
                    action="open",
                    payload="-",
                    message_or_callback=mock_callback,
                    user_id=mock_callback.from_user.id
                )
                
                # Проверяем результат
                assert result is True, "handle_action должен вернуть True"
                
                # Проверяем, что callback.answer был вызван (через ui_callback_handler)
                # Но здесь мы тестируем напрямую, поэтому проверяем navigate
                
                # Проверяем, что сообщение было отредактировано (edit_text вызван)
                # ИЛИ отправлено новое (send_message вызван)
                edit_called = mock_callback.message.edit_text.called
                send_called = mock_callback.bot.send_message.called
                
                assert edit_called or send_called, (
                    f"Сообщение должно быть либо отредактировано (edit_text), "
                    f"либо отправлено новое (send_message). "
                    f"edit_text called: {edit_called}, send_message called: {send_called}"
                )


@pytest.mark.asyncio
async def test_help_screen_from_crypto_payment(mock_callback, screen_manager):
    """
    Тест: открытие экрана помощи из криптоплатежа (после удаления сообщений)
    Ожидается: отправляется новое сообщение с экраном помощи
    """
    # Симулируем ситуацию, когда сообщение было удалено
    mock_callback.message.delete = AsyncMock()
    
    # Устанавливаем текущий экран
    screen_manager._set_current_screen(mock_callback.from_user.id, ScreenID.SUBSCRIPTION_PAYMENT)
    
    # Мокаем создание ViewModel
    with patch.object(screen_manager, '_create_viewmodel_for_screen') as mock_create_vm:
        mock_viewmodel = MagicMock(spec=BaseViewModel)
        mock_viewmodel.screen_id = ScreenID.HELP
        mock_create_vm.return_value = mock_viewmodel
        
        # Мокаем render и build_keyboard
        with patch('app.ui.screens.help.HelpScreen.render', new_callable=AsyncMock) as mock_render:
            mock_render.return_value = "ℹ️ Справка по CRS-VPN\n\n..."
            
            with patch('app.ui.screens.help.HelpScreen.build_keyboard', new_callable=AsyncMock) as mock_keyboard:
                from aiogram.types import InlineKeyboardMarkup
                mock_keyboard.return_value = InlineKeyboardMarkup(inline_keyboard=[])
                
                # Симулируем, что edit_text не работает (сообщение удалено)
                mock_callback.message.edit_text.side_effect = Exception("Message to edit not found")
                
                # Вызываем handle_action
                result = await screen_manager.handle_action(
                    screen_id=ScreenID.HELP,
                    action="open",
                    payload="-",
                    message_or_callback=mock_callback,
                    user_id=mock_callback.from_user.id
                )
                
                # Проверяем результат
                assert result is True, "handle_action должен вернуть True даже если edit не удался"
                
                # Проверяем, что было попытка отредактировать или отправить новое
                edit_called = mock_callback.message.edit_text.called
                send_called = mock_callback.bot.send_message.called
                
                assert edit_called or send_called, (
                    f"Должна быть попытка отредактировать или отправить новое сообщение. "
                    f"edit_text called: {edit_called}, send_message called: {send_called}"
                )


@pytest.mark.asyncio
async def test_help_screen_navigation_logic(mock_callback, screen_manager):
    """
    Тест: проверка логики навигации для HELP экрана
    Ожидается: HELP экран открывается как FLOW, не добавляется в backstack
    """
    # Устанавливаем текущий экран
    screen_manager._set_current_screen(mock_callback.from_user.id, ScreenID.SUBSCRIPTION_PAYMENT)
    
    # Проверяем, что HELP - это FLOW действие
    from app.ui.action_map import ACTION_MAP
    help_actions = ACTION_MAP.get(ScreenID.HELP)
    assert help_actions is not None, "HELP экран должен быть в ACTION_MAP"
    assert help_actions.get("open")[0] == "FLOW", "HELP экран должен открываться как FLOW"
    
    # Мокаем создание ViewModel
    with patch.object(screen_manager, '_create_viewmodel_for_screen') as mock_create_vm:
        mock_viewmodel = MagicMock(spec=BaseViewModel)
        mock_viewmodel.screen_id = ScreenID.HELP
        mock_create_vm.return_value = mock_viewmodel
        
        # Мокаем render и build_keyboard
        with patch('app.ui.screens.help.HelpScreen.render', new_callable=AsyncMock):
            with patch('app.ui.screens.help.HelpScreen.build_keyboard', new_callable=AsyncMock) as mock_keyboard:
                from aiogram.types import InlineKeyboardMarkup
                mock_keyboard.return_value = InlineKeyboardMarkup(inline_keyboard=[])
                
                # Вызываем handle_action
                result = await screen_manager.handle_action(
                    screen_id=ScreenID.HELP,
                    action="open",
                    payload="-",
                    message_or_callback=mock_callback,
                    user_id=mock_callback.from_user.id
                )
                
                assert result is True, "handle_action должен вернуть True"
                
                # Проверяем, что текущий экран изменился на HELP
                current_screen = screen_manager._get_current_screen(mock_callback.from_user.id)
                assert current_screen == ScreenID.HELP, (
                    f"Текущий экран должен быть HELP, но получили {current_screen}"
                )


@pytest.mark.asyncio
async def test_help_screen_ui_callback_handler(mock_callback):
    """
    Тест: проверка обработки UI callback для HELP экрана
    Ожидается: ui_callback_handler правильно обрабатывает ui:help:open:-
    """
    from app.ui.callbacks import parse_cb
    from app.routers.ui import ui_callback_handler
    
    # Парсим callback_data
    parsed = parse_cb(mock_callback.data)
    assert parsed is not None, "callback_data должен быть распарсен"
    screen_id, action, payload = parsed
    assert screen_id == ScreenID.HELP, f"screen_id должен быть HELP, но получили {screen_id}"
    assert action == "open", f"action должен быть 'open', но получили {action}"
    
    # Мокаем ScreenManager
    with patch('app.routers.ui.get_screen_manager') as mock_get_sm:
        mock_sm = MagicMock(spec=ScreenManager)
        mock_sm.handle_action = AsyncMock(return_value=True)
        mock_get_sm.return_value = mock_sm
        
        # Вызываем обработчик
        await ui_callback_handler(mock_callback)
        
        # Проверяем, что handle_action был вызван с правильными параметрами
        mock_sm.handle_action.assert_called_once_with(
            screen_id=ScreenID.HELP,
            action="open",
            payload="-",
            message_or_callback=mock_callback,
            user_id=mock_callback.from_user.id
        )
        
        # Проверяем, что callback.answer был вызван
        mock_callback.answer.assert_called_once()
