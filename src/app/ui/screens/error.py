"""
Экраны ошибок
"""
from typing import Optional
from aiogram import types
from app.ui.screens.base import BaseScreen
from app.ui.screens import ScreenID
from app.ui.viewmodels.error import (
    ErrorViewModel,
    AccessDeniedViewModel,
    RemnaUnavailableViewModel
)
from app.ui.renderers.error import (
    render_error,
    render_access_denied,
    render_remna_unavailable
)
from app.ui.keyboards.error import (
    build_error_keyboard,
    build_access_denied_keyboard,
    build_remna_unavailable_keyboard
)


class ErrorScreen(BaseScreen):
    """Экран общей ошибки"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ERROR
    
    async def render(self, viewmodel: ErrorViewModel) -> str:
        return await render_error(viewmodel)
    
    async def build_keyboard(self, viewmodel: ErrorViewModel) -> types.InlineKeyboardMarkup:
        return await build_error_keyboard(viewmodel)
    
    async def create_viewmodel(
        self,
        error_message: str = "Произошла ошибка",
        request_id: Optional[str] = None,
        error_type: str = "general"
    ) -> ErrorViewModel:
        return ErrorViewModel(
            error_message=error_message,
            request_id=request_id,
            error_type=error_type
        )


class AccessDeniedScreen(BaseScreen):
    """Экран отказа в доступе"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ACCESS_DENIED
    
    async def render(self, viewmodel: AccessDeniedViewModel) -> str:
        return await render_access_denied(viewmodel)
    
    async def build_keyboard(self, viewmodel: AccessDeniedViewModel) -> types.InlineKeyboardMarkup:
        return await build_access_denied_keyboard(viewmodel)
    
    async def create_viewmodel(
        self,
        reason: str = "У вас нет прав для доступа к этому экрану"
    ) -> AccessDeniedViewModel:
        return AccessDeniedViewModel(reason=reason)


class RemnaUnavailableScreen(BaseScreen):
    """Экран недоступности Remna"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.REMNA_UNAVAILABLE
    
    async def render(self, viewmodel: RemnaUnavailableViewModel) -> str:
        return await render_remna_unavailable(viewmodel)
    
    async def build_keyboard(self, viewmodel: RemnaUnavailableViewModel) -> types.InlineKeyboardMarkup:
        return await build_remna_unavailable_keyboard(viewmodel)
    
    async def create_viewmodel(
        self,
        message: str = "Сервис временно недоступен"
    ) -> RemnaUnavailableViewModel:
        return RemnaUnavailableViewModel(message=message)