"""
Keyboard builders для экранов подключения
"""
from aiogram import types
from typing import Optional
from app.ui.screens import ScreenID
from app.ui.callbacks import build_cb


def build_connect_success_keyboard(subscription_url: str) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру для успешного получения ссылки"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔗 Открыть ссылку", url=subscription_url)],
        [types.InlineKeyboardButton(
            text="⬅️ В главное меню",
            callback_data=build_cb(ScreenID.CONNECT, "back")
        )]
    ])


def build_connect_error_keyboard() -> types.InlineKeyboardMarkup:
    """Строит клавиатуру для ошибки подключения"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="📋 Подписка",
            callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "open")
        )],
        [types.InlineKeyboardButton(text="✍️ Написать администратору", url="https://t.me/dcfrq")],
        [types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=build_cb(ScreenID.CONNECT, "back")
        )]
    ])


def build_connect_no_subscription_keyboard() -> types.InlineKeyboardMarkup:
    """Строит клавиатуру для отсутствия подписки"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="📋 Подписка",
            callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "open")
        )],
        [types.InlineKeyboardButton(text="✍️ Написать администратору", url="https://t.me/dcfrq")],
        [types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=build_cb(ScreenID.CONNECT, "back")
        )]
    ])