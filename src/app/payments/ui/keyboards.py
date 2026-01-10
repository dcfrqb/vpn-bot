"""
Keyboards для payment UI

Единый стиль клавиатур для платежей.
Все клавиатуры платежей должны формироваться здесь, не в handlers.
"""
from aiogram import types
from app.ui.callbacks import build_cb
from app.ui.screens import ScreenID


def build_payment_keyboard(payment_url: str) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру для оплаты"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💳 Оплатить", url=payment_url)],
        [types.InlineKeyboardButton(
            text="⬅️ Назад к тарифам",
            callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "back")
        )]
    ])


def build_payment_error_keyboard() -> types.InlineKeyboardMarkup:
    """Строит клавиатуру для ошибки платежа"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="⬅️ Назад к тарифам",
            callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "back")
        )]
    ])


def build_crypto_payment_keyboard() -> types.InlineKeyboardMarkup:
    """Строит клавиатуру для крипто-платежа"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="📸 Отправить скриншот",
            callback_data="crypto_payment_screenshot"
        )],
        [types.InlineKeyboardButton(
            text="⬅️ Назад к тарифам",
            callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "back")
        )]
    ])