"""
Экран профиля пользователя
"""
from typing import Optional
from datetime import datetime
from aiogram import types
from app.ui.screens.base import BaseScreen
from app.ui.screens import ScreenID
from app.ui.viewmodels.profile import ProfileViewModel
from app.ui.renderers.profile import render_profile
from app.ui.keyboards.profile import build_profile_keyboard


class ProfileScreen(BaseScreen):
    """Экран профиля пользователя"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.PROFILE
    
    async def render(self, viewmodel: ProfileViewModel) -> str:
        """Рендерит текст экрана профиля"""
        return await render_profile(viewmodel)
    
    async def build_keyboard(self, viewmodel: ProfileViewModel) -> types.InlineKeyboardMarkup:
        """Строит клавиатуру для экрана профиля"""
        return await build_profile_keyboard(viewmodel)
    
    async def create_viewmodel(
        self,
        user_id: int,
        username: Optional[str] = None,
        created_at: Optional[datetime] = None,
        subscription_plan: Optional[str] = None,
        subscription_valid_until: Optional[datetime] = None,
        subscription_days_left: Optional[int] = None,
        total_payments: int = 0,
        total_spent: float = 0.0
    ) -> ProfileViewModel:
        """Создает ViewModel для экрана профиля"""
        return ProfileViewModel(
            user_id=user_id,
            username=username,
            created_at=created_at,
            subscription_plan=subscription_plan,
            subscription_valid_until=subscription_valid_until,
            subscription_days_left=subscription_days_left,
            total_payments=total_payments,
            total_spent=total_spent
        )