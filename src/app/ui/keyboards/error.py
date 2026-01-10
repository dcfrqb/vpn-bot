"""
Keyboard builders для экранов ошибок
"""
from aiogram import types
from app.ui.viewmodels.error import (
    ErrorViewModel,
    AccessDeniedViewModel,
    RemnaUnavailableViewModel
)
from app.ui.screens import ScreenID
from app.ui.callbacks import build_cb


async def build_error_keyboard(viewmodel: ErrorViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру для экрана ошибки"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="⬅️ В главное меню",
            callback_data=build_cb(ScreenID.MAIN_MENU, "back")
        )]
    ])


async def build_access_denied_keyboard(viewmodel: AccessDeniedViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру для экрана отказа в доступе"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="⬅️ В главное меню",
            callback_data=build_cb(ScreenID.MAIN_MENU, "back")
        )]
    ])


async def build_remna_unavailable_keyboard(viewmodel: RemnaUnavailableViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру для экрана недоступности Remna"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="🔄 Попробовать снова",
            callback_data=build_cb(ScreenID.MAIN_MENU, "refresh")
        )],
        [types.InlineKeyboardButton(
            text="⬅️ В главное меню",
            callback_data=build_cb(ScreenID.MAIN_MENU, "back")
        )]
    ])