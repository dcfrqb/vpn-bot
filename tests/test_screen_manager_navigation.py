"""
Тесты для проверки навигации и обработки действий в ScreenManager
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram import types
from app.ui.screen_manager import ScreenManager
from app.ui.screens import ScreenID
from app.ui.viewmodels.main_menu import MainMenuViewModel
from app.ui.viewmodels.subscription import SubscriptionViewModel


@pytest.fixture
def screen_manager():
    """Создает экземпляр ScreenManager для тестов"""
    return ScreenManager()


@pytest.fixture
def mock_callback_query():
    """Создает мок CallbackQuery"""
    callback = MagicMock(spec=types.CallbackQuery)
    # Явно создаем from_user как MagicMock
    callback.from_user = MagicMock()
    callback.from_user.id = 12345
    callback.from_user.first_name = "Test"
    callback.from_user.last_name = "User"
    callback.from_user.username = "testuser"
    callback.data = "ui:main_menu:open:-"
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


@pytest.mark.asyncio
async def test_back_action_with_backstack(screen_manager, mock_callback_query):
    """Тест: действие 'back' должно вернуть на предыдущий экран из backstack"""
    user_id = 12345
    
    # Устанавливаем MAIN_MENU как текущий, затем переходим на SUBSCRIPTION_PLANS
    screen_manager._set_current_screen(user_id, ScreenID.MAIN_MENU)
    screen_manager._push_to_backstack(user_id, ScreenID.MAIN_MENU)
    screen_manager._set_current_screen(user_id, ScreenID.SUBSCRIPTION_PLANS)
    
    # Мокаем необходимые зависимости
    mock_main_menu_screen = MagicMock()
    mock_main_menu_screen.create_viewmodel = AsyncMock(return_value=MainMenuViewModel(user_id=user_id))
    mock_main_menu_screen.render = AsyncMock(return_value="Main Menu")
    mock_main_menu_screen.build_keyboard = AsyncMock(return_value=MagicMock())
    
    with patch.object(screen_manager, 'get_screen', return_value=mock_main_menu_screen), \
         patch.object(screen_manager, 'show_screen', new_callable=AsyncMock) as mock_show, \
         patch('app.ui.helpers.get_main_menu_viewmodel', new_callable=AsyncMock) as mock_get_vm:
        
        mock_show.return_value = True
        mock_get_vm.return_value = MainMenuViewModel(user_id=user_id)
        
        # Вызываем handle_action с действием "back"
        result = await screen_manager.handle_action(
            screen_id=ScreenID.SUBSCRIPTION_PLANS,
            action="back",
            payload="-",
            message_or_callback=mock_callback_query,
            user_id=user_id
        )
        
        # Проверяем контракт: handle_action должен вернуть True и вызвать show_screen
        assert result is True
        assert mock_show.called


@pytest.mark.asyncio
async def test_back_action_without_backstack(screen_manager, mock_callback_query):
    """Тест: действие 'back' без backstack должно вернуть в MAIN_MENU"""
    user_id = 12345
    
    # Устанавливаем текущий экран, но backstack пуст
    screen_manager._set_current_screen(user_id, ScreenID.SUBSCRIPTION_PLANS)
    
    with patch('app.ui.helpers.get_main_menu_viewmodel', new_callable=AsyncMock) as mock_get_vm, \
         patch.object(screen_manager, 'show_screen', new_callable=AsyncMock) as mock_show:
        
        mock_get_vm.return_value = MainMenuViewModel(user_id=user_id)
        mock_show.return_value = True
        
        result = await screen_manager.handle_action(
            screen_id=ScreenID.SUBSCRIPTION_PLANS,
            action="back",
            payload="-",
            message_or_callback=mock_callback_query,
            user_id=user_id
        )
        
        # Проверяем контракт: handle_action должен вернуть True
        assert result is True
        assert mock_show.called


@pytest.mark.asyncio
async def test_open_action_edits_message(screen_manager, mock_callback_query):
    """Тест: действие 'open' должно редактировать сообщение, если есть текущий экран"""
    user_id = 12345
    
    # Устанавливаем текущий экран (пользователь уже на каком-то экране)
    screen_manager._set_current_screen(user_id, ScreenID.MAIN_MENU)
    
    # Мокаем экран CONNECT
    mock_connect_screen = MagicMock()
    mock_connect_screen.create_viewmodel = AsyncMock(return_value=MagicMock())
    mock_connect_screen.render = AsyncMock(return_value="Connect Screen")
    mock_connect_screen.build_keyboard = AsyncMock(return_value=MagicMock())
    
    with patch.object(screen_manager, 'get_screen', return_value=mock_connect_screen), \
         patch.object(screen_manager, 'show_screen', new_callable=AsyncMock) as mock_show:
        
        mock_show.return_value = True
        
        result = await screen_manager.handle_action(
            screen_id=ScreenID.CONNECT,
            action="open",
            payload="-",
            message_or_callback=mock_callback_query,
            user_id=user_id
        )
        
        # Проверяем контракт: handle_action должен вернуть True и вызвать show_screen
        assert result is True
        assert mock_show.called


@pytest.mark.asyncio
async def test_open_action_sends_new_message(screen_manager, mock_callback_query):
    """Тест: действие 'open' должно отправить новое сообщение, если нет текущего экрана"""
    user_id = 12345
    
    # Нет текущего экрана (первый запуск)
    assert screen_manager._get_current_screen(user_id) is None
    
    # Мокаем экран CONNECT
    mock_connect_screen = MagicMock()
    mock_connect_screen.create_viewmodel = AsyncMock(return_value=MagicMock())
    mock_connect_screen.render = AsyncMock(return_value="Connect Screen")
    mock_connect_screen.build_keyboard = AsyncMock(return_value=MagicMock())
    
    with patch.object(screen_manager, 'get_screen', return_value=mock_connect_screen), \
         patch.object(screen_manager, 'show_screen', new_callable=AsyncMock) as mock_show:
        
        mock_show.return_value = True
        
        result = await screen_manager.handle_action(
            screen_id=ScreenID.CONNECT,
            action="open",
            payload="-",
            message_or_callback=mock_callback_query,
            user_id=user_id
        )
        
        # Проверяем контракт: handle_action должен вернуть True и вызвать show_screen
        assert result is True
        assert mock_show.called


@pytest.mark.asyncio
async def test_refresh_action_edits_message(screen_manager, mock_callback_query):
    """Тест: действие 'refresh' должно редактировать сообщение"""
    user_id = 12345
    
    # Устанавливаем текущий экран
    screen_manager._set_current_screen(user_id, ScreenID.MAIN_MENU)
    
    # Мокаем экран MAIN_MENU
    mock_main_menu_screen = MagicMock()
    mock_main_menu_screen.create_viewmodel = AsyncMock(return_value=MainMenuViewModel(user_id=user_id))
    mock_main_menu_screen.render = AsyncMock(return_value="Main Menu")
    mock_main_menu_screen.build_keyboard = AsyncMock(return_value=MagicMock())
    
    with patch.object(screen_manager, 'get_screen', return_value=mock_main_menu_screen), \
         patch('app.ui.helpers.get_main_menu_viewmodel', new_callable=AsyncMock) as mock_get_vm, \
         patch.object(screen_manager, 'show_screen', new_callable=AsyncMock) as mock_show:
        
        mock_get_vm.return_value = MainMenuViewModel(user_id=user_id)
        mock_show.return_value = True
        
        result = await screen_manager.handle_action(
            screen_id=ScreenID.MAIN_MENU,
            action="refresh",
            payload="-",
            message_or_callback=mock_callback_query,
            user_id=user_id
        )
        
        # Проверяем контракт: handle_action должен вернуть True и вызвать show_screen
        assert result is True
        assert mock_show.called


def test_backstack_push_and_pop(screen_manager):
    """Тест: проверка работы backstack (push и pop)"""
    user_id = 12345
    
    # Добавляем экраны в backstack
    screen_manager._set_current_screen(user_id, ScreenID.MAIN_MENU)
    screen_manager._push_to_backstack(user_id, ScreenID.SUBSCRIPTION_PLANS)
    screen_manager._set_current_screen(user_id, ScreenID.SUBSCRIPTION_PLANS)
    screen_manager._push_to_backstack(user_id, ScreenID.SUBSCRIPTION_PLAN_DETAIL)
    screen_manager._set_current_screen(user_id, ScreenID.SUBSCRIPTION_PLAN_DETAIL)
    
    # Проверяем, что backstack содержит правильные экраны
    backstack = screen_manager._backstacks.get(user_id, [])
    assert ScreenID.SUBSCRIPTION_PLANS in backstack
    assert ScreenID.MAIN_MENU in backstack
    
    # Извлекаем предыдущий экран
    prev_screen = screen_manager._pop_from_backstack(user_id)
    assert prev_screen == ScreenID.SUBSCRIPTION_PLANS
    
    # Извлекаем еще один
    prev_screen2 = screen_manager._pop_from_backstack(user_id)
    assert prev_screen2 == ScreenID.MAIN_MENU
