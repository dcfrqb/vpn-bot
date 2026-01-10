"""
Экран подключения VPN
"""
from typing import Optional
from aiogram import types
from app.ui.screens.base import BaseScreen
from app.ui.screens import ScreenID
from app.ui.viewmodels.connect import ConnectViewModel
from app.ui.renderers.connect import (
    render_connect_loading,
    render_connect_success,
    render_connect_error,
    render_connect_no_subscription
)
from app.ui.keyboards.connect import (
    build_connect_success_keyboard,
    build_connect_error_keyboard,
    build_connect_no_subscription_keyboard
)


class ConnectScreen(BaseScreen):
    """Экран подключения VPN"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.CONNECT
    
    async def render(self, viewmodel: ConnectViewModel) -> str:
        """Рендерит текст экрана подключения"""
        if viewmodel.is_loading:
            return await render_connect_loading()
        elif viewmodel.is_success:
            return await render_connect_success(viewmodel.subscription_url)
        elif viewmodel.has_no_subscription:
            return await render_connect_no_subscription()
        else:  # error
            return await render_connect_error(viewmodel.error_message)
    
    async def build_keyboard(self, viewmodel: ConnectViewModel) -> types.InlineKeyboardMarkup:
        """Строит клавиатуру для экрана подключения"""
        if viewmodel.is_success:
            return build_connect_success_keyboard(viewmodel.subscription_url)
        elif viewmodel.has_no_subscription:
            return build_connect_no_subscription_keyboard()
        else:  # error or loading
            return build_connect_error_keyboard()
    
    async def create_viewmodel(
        self,
        has_subscription: bool,
        subscription_url: Optional[str] = None,
        status: str = "loading",
        error_message: Optional[str] = None
    ) -> ConnectViewModel:
        """Создает ViewModel для экрана подключения"""
        return ConnectViewModel(
            has_subscription=has_subscription,
            subscription_url=subscription_url,
            status=status,
            error_message=error_message
        )