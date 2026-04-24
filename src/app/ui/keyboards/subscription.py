"""
Keyboard builders для экранов подписки
"""
from aiogram import types
from app.ui.viewmodels.subscription import (
    SubscriptionViewModel,
    SubscriptionPlanDetailViewModel,
    SubscriptionPaymentViewModel
)
from app.ui.screens import ScreenID
from app.ui.callbacks import build_cb


async def build_subscription_plans_keyboard(viewmodel: SubscriptionViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру выбора тарифов"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="Базовый - от 99₽/мес",
            callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "select", "basic")
        )],
        [types.InlineKeyboardButton(
            text="Премиум - от 199₽/мес",
            callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "select", "premium")
        )],
        [types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=build_cb(viewmodel.screen_id, "back")
        )]
    ])


async def build_subscription_plan_detail_keyboard(viewmodel: SubscriptionPlanDetailViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру детального экрана тарифа"""
    keyboard = []
    
    # Кнопки выбора периода подписки
    if viewmodel.plan_code == "basic":
        periods = [
            ("1 месяц - 99₽", "1"),
            ("3 месяца - 249₽", "3"),
            ("6 месяцев - 499₽", "6"),
            ("12 месяцев - 899₽", "12")
        ]
    else:  # premium
        periods = [
            ("1 месяц - 199₽", "1"),
            ("3 месяца - 549₽", "3"),
            ("6 месяцев - 999₽", "6"),
            ("12 месяцев - 1799₽", "12")
        ]
    
    # Добавляем кнопки периодов
    for period_text, period_months in periods:
        keyboard.append([types.InlineKeyboardButton(
            text=period_text,
            callback_data=build_cb(viewmodel.screen_id, "select_period", f"{viewmodel.plan_code}_{period_months}")
        )])
    
    # Разделитель
    keyboard.append([])
    
    # Кнопки оплаты (показываем только если период выбран)
    if viewmodel.period_months > 0:
        keyboard.append([types.InlineKeyboardButton(
            text="💳 Оплатить картой (Yookassa)",
            callback_data=f"pay_yookassa_{viewmodel.plan_code}_{viewmodel.period_months}_{viewmodel.amount}"
        )])
    
    keyboard.append([types.InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=build_cb(viewmodel.screen_id, "back")
    )])
    
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


async def build_subscription_payment_keyboard(viewmodel: SubscriptionPaymentViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру экрана оплаты (кнопка «Проверить» привязана к external_id)"""
    keyboard = []
    
    if viewmodel.payment_url:
        keyboard.append([types.InlineKeyboardButton(text="💳 Оплатить", url=viewmodel.payment_url)])
        if viewmodel.external_id:
            keyboard.append([types.InlineKeyboardButton(
                text="🔄 Проверить оплату",
                callback_data=f"check_payment:{viewmodel.external_id}"
            )])
    
    # Кнопка помощи
    keyboard.append([types.InlineKeyboardButton(
        text="ℹ️ Помощь",
        callback_data=build_cb(ScreenID.HELP, "open")
    )])
    
    keyboard.append([types.InlineKeyboardButton(
        text="⬅️ Назад к тарифам",
        callback_data=build_cb(viewmodel.screen_id, "back")
    )])
    
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_period_keyboard(plan_code: str) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру выбора периода подписки"""
    # ВАЖНО: Это legacy функция, используется в старых обработчиках
    # TODO: Мигрировать на новый формат после обновления обработчиков
    # Используем SUBSCRIPTION_PLAN_DETAIL как текущий экран для кнопки "назад"
    if plan_code == "basic":
        keyboard = [
            [types.InlineKeyboardButton(text="1 месяц - 99₽", callback_data="plan_basic_1")],
            [types.InlineKeyboardButton(text="3 месяца - 249₽", callback_data="plan_basic_3")],
            [types.InlineKeyboardButton(text="6 месяцев - 499₽", callback_data="plan_basic_6")],
            [types.InlineKeyboardButton(text="12 месяцев - 899₽", callback_data="plan_basic_12")],
            [types.InlineKeyboardButton(
                text="⬅️ Назад к тарифам",
                callback_data=build_cb(ScreenID.SUBSCRIPTION_PLAN_DETAIL, "back")
            )]
        ]
    else:  # premium
        keyboard = [
            [types.InlineKeyboardButton(text="1 месяц - 199₽", callback_data="plan_premium_1")],
            [types.InlineKeyboardButton(text="3 месяца - 549₽", callback_data="plan_premium_3")],
            [types.InlineKeyboardButton(text="6 месяцев - 999₽", callback_data="plan_premium_6")],
            [types.InlineKeyboardButton(text="12 месяцев - 1799₽", callback_data="plan_premium_12")],
            [types.InlineKeyboardButton(
                text="⬅️ Назад к тарифам",
                callback_data=build_cb(ScreenID.SUBSCRIPTION_PLAN_DETAIL, "back")
            )]
        ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)