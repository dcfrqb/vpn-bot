"""
Keyboard builders для экранов подписки.

Меню тарифов одинаковое для всех — `MENU_PLAN_CODES` (lite/standard/pro).
Если у юзера есть последняя покупаемая подписка — над тарифами добавляется
кнопка "🔄 Продлить {имя тарифа}", ведущая на детальный экран этого тарифа
с актуальными для него ценами (legacy → старые, new → новые).
"""
from aiogram import types

from app.core.plans import (
    MENU_PLAN_CODES,
    get_plan_name,
    get_plan_price,
)
from app.ui.callbacks import build_cb
from app.ui.screens import ScreenID
from app.ui.viewmodels.subscription import (
    SubscriptionPaymentViewModel,
    SubscriptionPlanDetailViewModel,
    SubscriptionViewModel,
)


# Канонический порядок периодов в детальном экране.
PERIOD_MONTHS: tuple[int, ...] = (1, 3, 6, 12)


def _plan_button_text(plan_code: str) -> str:
    name = get_plan_name(plan_code)
    price = get_plan_price(plan_code, 1)
    return f"{name} - от {price}₽/мес"


async def build_subscription_plans_keyboard(
    viewmodel: SubscriptionViewModel,
) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру выбора тарифов.

    Если у юзера есть последний план (`viewmodel.last_plan_code`) — первой
    строкой кнопка "🔄 Продлить {tariff}". Дальше — фиксированные 3 новых тарифа.
    """
    keyboard: list[list[types.InlineKeyboardButton]] = []

    if viewmodel.last_plan_code:
        keyboard.append([
            types.InlineKeyboardButton(
                text="🔄 Продлить",
                callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "extend"),
            )
        ])

    for code in MENU_PLAN_CODES:
        keyboard.append([
            types.InlineKeyboardButton(
                text=_plan_button_text(code),
                callback_data=build_cb(ScreenID.SUBSCRIPTION_PLANS, "select", code),
            )
        ])

    keyboard.append([
        types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=build_cb(viewmodel.screen_id, "back"),
        )
    ])
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


async def build_subscription_plan_detail_keyboard(
    viewmodel: SubscriptionPlanDetailViewModel,
) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру детального экрана тарифа.

    Кнопки периодов рендерятся из PLAN_CATALOG по plan_code из viewmodel —
    одинаково для legacy и new (цены берутся правильные).
    """
    keyboard: list[list[types.InlineKeyboardButton]] = []

    for months in PERIOD_MONTHS:
        price = get_plan_price(viewmodel.plan_code, months)
        if price <= 0:
            # Невалидный тариф/период — пропускаем кнопку.
            continue
        period_label = f"{months} месяц" if months == 1 else f"{months} месяцев"
        keyboard.append([
            types.InlineKeyboardButton(
                text=f"{period_label} - {price}₽",
                callback_data=build_cb(
                    viewmodel.screen_id,
                    "select_period",
                    f"{viewmodel.plan_code}_{months}",
                ),
            )
        ])

    keyboard.append([])  # визуальный разделитель

    if viewmodel.period_months > 0:
        keyboard.append([
            types.InlineKeyboardButton(
                text="💳 Оплатить картой (Yookassa)",
                callback_data=(
                    f"pay_yookassa_{viewmodel.plan_code}_"
                    f"{viewmodel.period_months}_{viewmodel.amount}"
                ),
            )
        ])

    keyboard.append([
        types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=build_cb(viewmodel.screen_id, "back"),
        )
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


async def build_subscription_payment_keyboard(
    viewmodel: SubscriptionPaymentViewModel,
) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру экрана оплаты (кнопка «Проверить» привязана к external_id)."""
    keyboard: list[list[types.InlineKeyboardButton]] = []

    if viewmodel.payment_url:
        keyboard.append([types.InlineKeyboardButton(text="💳 Оплатить", url=viewmodel.payment_url)])
        if viewmodel.external_id:
            keyboard.append([types.InlineKeyboardButton(
                text="🔄 Проверить оплату",
                callback_data=f"check_payment:{viewmodel.external_id}",
            )])

    keyboard.append([types.InlineKeyboardButton(
        text="ℹ️ Помощь",
        callback_data=build_cb(ScreenID.HELP, "open"),
    )])

    keyboard.append([types.InlineKeyboardButton(
        text="⬅️ Назад к тарифам",
        callback_data=build_cb(viewmodel.screen_id, "back"),
    )])

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_period_keyboard(plan_code: str) -> types.InlineKeyboardMarkup:
    """LEGACY: клавиатура выбора периода со старыми callback'ами plan_basic_*.

    Используется в legacy-обработчиках (routers/start.py и legacy_callbacks.py).
    Не трогаем — старые stale callback'и у legacy-юзеров продолжают работать.
    """
    if plan_code == "basic":
        keyboard = [
            [types.InlineKeyboardButton(text="1 месяц - 99₽", callback_data="plan_basic_1")],
            [types.InlineKeyboardButton(text="3 месяца - 249₽", callback_data="plan_basic_3")],
            [types.InlineKeyboardButton(text="6 месяцев - 499₽", callback_data="plan_basic_6")],
            [types.InlineKeyboardButton(text="12 месяцев - 899₽", callback_data="plan_basic_12")],
            [types.InlineKeyboardButton(
                text="⬅️ Назад к тарифам",
                callback_data=build_cb(ScreenID.SUBSCRIPTION_PLAN_DETAIL, "back"),
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
                callback_data=build_cb(ScreenID.SUBSCRIPTION_PLAN_DETAIL, "back"),
            )]
        ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)
