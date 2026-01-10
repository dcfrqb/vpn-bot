"""
Экран помощи
"""
from aiogram import types
from app.ui.screens.base import BaseScreen
from app.ui.viewmodels.base import BaseViewModel
from app.ui.screens import ScreenID
from app.ui.renderers.help import render_help
from app.ui.keyboards.help import build_help_keyboard


class HelpScreen(BaseScreen):
    """Экран помощи"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.HELP
    
    async def render(self, viewmodel: BaseViewModel) -> str:
        return await render_help(viewmodel)
    
    async def build_keyboard(self, viewmodel: BaseViewModel) -> types.InlineKeyboardMarkup:
        return await build_help_keyboard(viewmodel)
    
    async def create_viewmodel(self, **kwargs) -> BaseViewModel:
        # Help screen не требует данных
        class EmptyViewModel(BaseViewModel):
            @property
            def screen_id(self) -> ScreenID:
                return ScreenID.HELP
        
        return EmptyViewModel()