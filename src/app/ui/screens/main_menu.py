"""
Экран главного меню
"""
from typing import Optional, Union
from aiogram import types
from app.ui.screens.base import BaseScreen
from app.ui.viewmodels.base import BaseViewModel
from app.ui.screens import ScreenID
from app.ui.viewmodels.main_menu import MainMenuViewModel
from app.ui.renderers.main_menu import render_main_menu
from app.ui.keyboards.main_menu import build_main_menu_keyboard
from app.routers.subscription_view import SubscriptionViewModel
from app.config import is_admin
from app.logger import logger


class MainMenuScreen(BaseScreen):
    """Экран главного меню"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.MAIN_MENU
    
    async def render(self, viewmodel: MainMenuViewModel) -> str:
        """Рендерит текст главного меню"""
        return await render_main_menu(viewmodel)
    
    async def build_keyboard(self, viewmodel: MainMenuViewModel) -> types.InlineKeyboardMarkup:
        """Строит клавиатуру главного меню"""
        return await build_main_menu_keyboard(viewmodel)
    
    async def create_viewmodel(
        self,
        user_id: int = 0,
        user_first_name: Optional[str] = None,
        user_last_name: Optional[str] = None,
        user_username: Optional[str] = None,
        subscription_view_model: Optional[SubscriptionViewModel] = None
    ) -> MainMenuViewModel:
        """Создает ViewModel для главного меню"""
        return MainMenuViewModel(
            user_id=user_id,
            user_first_name=user_first_name,
            user_last_name=user_last_name,
            user_username=user_username,
            subscription_view_model=subscription_view_model,
            is_admin=is_admin(user_id) if user_id else False
        )
    
    async def handle_action(
        self,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int]
    ) -> bool:
        """Обрабатывает действия экрана (refresh - обновление информации)"""
        from app.ui.screen_manager import get_screen_manager
        from app.ui.helpers import get_main_menu_viewmodel
        
        if action == "refresh":
            # Обновление главного меню
            if user_id is None:
                return False
            
            # Получаем обновленные данные
            if isinstance(message_or_callback, types.CallbackQuery):
                viewmodel = await get_main_menu_viewmodel(
                    telegram_id=user_id,
                    first_name=message_or_callback.from_user.first_name,
                    last_name=message_or_callback.from_user.last_name,
                    username=message_or_callback.from_user.username,
                    use_cache=False,
                    force_sync=True  # Принудительная синхронизация
                )
            elif isinstance(message_or_callback, types.Message):
                viewmodel = await get_main_menu_viewmodel(
                    telegram_id=user_id,
                    first_name=message_or_callback.from_user.first_name,
                    last_name=message_or_callback.from_user.last_name,
                    username=message_or_callback.from_user.username,
                    use_cache=False,
                    force_sync=True
                )
            else:
                return False
            
            # STATE action - show_screen с edit=True, НЕ navigate
            screen_manager = get_screen_manager()
            return await screen_manager.show_screen(
                screen_id=ScreenID.MAIN_MENU,
                message_or_callback=message_or_callback,
                viewmodel=viewmodel,
                edit=True,
                user_id=user_id
            )
        
        return False