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


def build_connect_success_keyboard_with_obhod(
    subscription_url: str,
    is_pro: bool,
    obhod_url: Optional[str] = None,
    show_more_obhod: bool = False,
) -> types.InlineKeyboardMarkup:
    """Клавиатура экрана «Подключиться» с обходом.

    Кнопки: открыть основную ссылку, открыть ссылку обхода (если есть),
    «➕ Нужно больше обхода» (для Pro → покупка пакета), назад.
    """
    rows: list[list[types.InlineKeyboardButton]] = [
        [types.InlineKeyboardButton(text="🔗 Открыть основную ссылку", url=subscription_url)],
    ]
    if is_pro and obhod_url:
        rows.append(
            [types.InlineKeyboardButton(text="🛡 Открыть ссылку обхода", url=obhod_url)]
        )
    if is_pro and show_more_obhod:
        # Ведем в категорию пакетов обхода внутри экрана подписки.
        rows.append([types.InlineKeyboardButton(
            text="➕ Нужно больше обхода",
            callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "obhod"),
        )])
    rows.append([types.InlineKeyboardButton(
        text="⬅️ В главное меню",
        callback_data=build_cb(ScreenID.CONNECT, "back"),
    )])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


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