"""
Keyboard builder для экрана помощи
"""
from aiogram import types
from app.ui.viewmodels.base import BaseViewModel
from app.ui.screens import ScreenID
from app.ui.callbacks import build_cb


async def build_help_keyboard(viewmodel: BaseViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру экрана помощи"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="✍️ Написать администратору",
            url="https://t.me/dcfrq"
        )],
        [types.InlineKeyboardButton(
            text="📄 Оферта",
            url="https://telegra.ph/Publichnaya-oferta--CRS-VPN-04-08"
        )],
        [types.InlineKeyboardButton(
            text="🔒 Политика конфиденциальности",
            url="https://telegra.ph/Politika-konfidencialnosti--CRS-VPN-04-08"
        )],
        [types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=build_cb(viewmodel.screen_id, "back")
        )]
    ])