"""Модуль для создания клавиатур бота"""
from aiogram import types
from typing import Optional
from app.config import is_admin


def get_main_menu_keyboard(user_id: Optional[int] = None, has_subscription: bool = False) -> types.InlineKeyboardMarkup:
    """Главное меню бота"""
    keyboard = []
    
    # Для активного пользователя (с подпиской) - кнопка "Подключиться"
    if has_subscription:
        keyboard.append([types.InlineKeyboardButton(text="🚀 Подключиться", callback_data="connect_vpn")])
    
    # Кнопка "Подписка" - всегда есть
    keyboard.append([types.InlineKeyboardButton(text="💳 Подписка", callback_data="buy_subscription")])
    
    # Кнопка "Обновить" - всегда есть
    keyboard.append([types.InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_info")])
    
    # Кнопка "Помощь" - всегда есть
    keyboard.append([types.InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")])
    
    # Для админа - кнопка "Админ-панель" в конце
    if user_id and is_admin(user_id):
        keyboard.append([types.InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_plans_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура выбора тарифов"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Базовый - от 99₽/мес", callback_data="plan_basic")],
        [types.InlineKeyboardButton(text="Премиум - от 199₽/мес", callback_data="plan_premium")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])

def get_period_keyboard(plan_code: str) -> types.InlineKeyboardMarkup:
    """Клавиатура выбора периода подписки"""
    if plan_code == "basic":
        keyboard = [
            [types.InlineKeyboardButton(text="1 месяц - 99₽", callback_data="plan_basic_1")],
            [types.InlineKeyboardButton(text="3 месяца - 249₽", callback_data="plan_basic_3")],
            [types.InlineKeyboardButton(text="6 месяцев - 499₽", callback_data="plan_basic_6")],
            [types.InlineKeyboardButton(text="12 месяцев - 899₽", callback_data="plan_basic_12")],
            [types.InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="buy_subscription")]
        ]
    else:  # premium
        keyboard = [
            [types.InlineKeyboardButton(text="1 месяц - 199₽", callback_data="plan_premium_1")],
            [types.InlineKeyboardButton(text="3 месяца - 549₽", callback_data="plan_premium_3")],
            [types.InlineKeyboardButton(text="6 месяцев - 999₽", callback_data="plan_premium_6")],
            [types.InlineKeyboardButton(text="12 месяцев - 1799₽", callback_data="plan_premium_12")],
            [types.InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="buy_subscription")]
        ]
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_payment_method_keyboard(plan_code: str, period_months: int = 1, amount: int = 0) -> types.InlineKeyboardMarkup:
    """Клавиатура выбора способа оплаты"""
    keyboard = []
    
    # Кнопка оплаты через Yookassa (карта)
    keyboard.append([types.InlineKeyboardButton(
        text="💳 Оплатить картой (Yookassa)",
        callback_data=f"pay_yookassa_{plan_code}_{period_months}_{amount}"
    )])
    
    # Кнопка оплаты криптовалютой (если настроен адрес)
    from app.config import settings
    if settings.CRYPTO_USDT_TRC20_ADDRESS:
        keyboard.append([types.InlineKeyboardButton(
            text="₿ Оплатить USDT (TRC20)",
            callback_data=f"pay_crypto_{plan_code}_{period_months}_{amount}"
        )])
    
    keyboard.append([types.InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=f"plan_{plan_code}"
    )])
    
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_payment_keyboard(payment_url: str) -> types.InlineKeyboardMarkup:
    """Клавиатура для оплаты"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💳 Оплатить", url=payment_url)],
        [types.InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="buy_subscription")]
    ])


def get_back_to_plans_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура с кнопкой возврата к тарифам"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="buy_subscription")]
    ])


def get_help_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура для справки"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
    ])


def get_subscription_info_keyboard(has_subscription: bool = False) -> types.InlineKeyboardMarkup:
    """Клавиатура для информации о подписке"""
    if has_subscription:
        return types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔗 Получить ссылку", callback_data="get_subscription_link")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ])
    else:
        return types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="💳 Выбрать тариф", callback_data="buy_subscription")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ])


def get_admin_panel_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура админ-панели"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [types.InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [types.InlineKeyboardButton(text="💳 Платежи", callback_data="admin_payments")],
        [types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main")]
    ])


def get_admin_back_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура возврата в админ-панель"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
    ])


def get_admin_stats_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура для статистики"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats")],
        [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
    ])


def get_users_pagination_keyboard(page: int, total_pages: int) -> list:
    """Клавиатура пагинации для списка пользователей"""
    keyboard = []
    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Предыдущая", callback_data=f"admin_users_page_{page - 1}"
            ))
        if page < total_pages:
            nav_buttons.append(types.InlineKeyboardButton(
                text="Следующая ➡️", callback_data=f"admin_users_page_{page + 1}"
            ))
        if nav_buttons:
            keyboard.append(nav_buttons)
    keyboard.append([types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")])
    return keyboard


def get_payments_pagination_keyboard(page: int, total_pages: int, status: Optional[str] = None) -> list:
    """Клавиатура пагинации и фильтров для списка платежей"""
    keyboard = []
    if total_pages > 1:
        nav_buttons = []
        status_suffix = f"_{status or 'all'}" if status else ""
        if page > 1:
            nav_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Предыдущая", 
                callback_data=f"admin_payments_page_{page - 1}{status_suffix}"
            ))
        if page < total_pages:
            nav_buttons.append(types.InlineKeyboardButton(
                text="Следующая ➡️", 
                callback_data=f"admin_payments_page_{page + 1}{status_suffix}"
            ))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="📊 Все", callback_data="admin_payments_all")],
        [types.InlineKeyboardButton(text="✅ Успешные", callback_data="admin_payments_succeeded")],
        [types.InlineKeyboardButton(text="⏳ Ожидают", callback_data="admin_payments_pending")],
        [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
    ])
    return keyboard


def get_subscription_link_keyboard() -> types.InlineKeyboardMarkup:
    """Клавиатура для получения ссылки подписки"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔗 Получить ссылку", callback_data="get_subscription_link")],
        [types.InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")]
    ])

