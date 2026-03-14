"""
Keyboard builder для главного меню
"""
from aiogram import types
from app.ui.viewmodels.main_menu import MainMenuViewModel
from app.ui.screens import ScreenID
from app.ui.callbacks import build_cb
from app.config import is_admin


async def build_main_menu_keyboard(viewmodel: MainMenuViewModel) -> types.InlineKeyboardMarkup:
    """
    Строит клавиатуру главного меню
    
    Args:
        viewmodel: MainMenuViewModel с данными
        
    Returns:
        InlineKeyboardMarkup
    """
    keyboard = []
    
    # Кнопка "Подключиться" - всегда видна
    keyboard.append([types.InlineKeyboardButton(
        text="🚀 Подключиться",
        callback_data=build_cb(ScreenID.CONNECT, "open")
    )])
    
    # Кнопка "Подписка" - всегда есть
    keyboard.append([types.InlineKeyboardButton(
        text="💳 Подписка",
        callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "open")
    )])
    
    # Кнопки "Обновить" и "Помощь" в одной строке — вспомогательные действия
    keyboard.append([
        types.InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=build_cb(ScreenID.MAIN_MENU, "refresh")
        ),
        types.InlineKeyboardButton(
            text="ℹ️ Помощь",
            callback_data=build_cb(ScreenID.HELP, "open")
        ),
    ])
    
    # Для админа - кнопка "Админ-панель" в конце
    if viewmodel.is_admin:
        keyboard.append([types.InlineKeyboardButton(
            text="👑 Админ-панель",
            callback_data=build_cb(ScreenID.ADMIN_PANEL, "open")
        )])
    
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)