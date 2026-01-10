"""
Keyboard builder для экрана профиля
"""
from aiogram import types
from app.ui.viewmodels.profile import ProfileViewModel
from app.ui.screens import ScreenID
from app.ui.callbacks import build_cb


async def build_profile_keyboard(viewmodel: ProfileViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру для экрана профиля"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="⬅️ В главное меню",
            callback_data=build_cb(viewmodel.screen_id, "back")
        )]
    ])