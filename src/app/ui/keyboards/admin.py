"""
Keyboard builders для админских экранов
"""
from aiogram import types
from typing import Optional
from app.ui.viewmodels.admin import (
    AdminPanelViewModel,
    AdminStatsViewModel,
    AdminUsersViewModel,
    AdminPaymentsViewModel
)
from app.ui.screens import ScreenID
from app.ui.callbacks import build_cb


async def build_admin_panel_keyboard(viewmodel: AdminPanelViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру главной панели администратора"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="👥 Пользователи",
            callback_data=build_cb(ScreenID.ADMIN_USERS, "open")
        )],
        [types.InlineKeyboardButton(
            text="💳 Платежи",
            callback_data=build_cb(ScreenID.ADMIN_PAYMENTS, "open")
        )],
        [types.InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=build_cb(ScreenID.ADMIN_PANEL, "refresh")
        )],
        [types.InlineKeyboardButton(text="🔗 Панель", url="https://panel1.crs-projects.com")],
        [types.InlineKeyboardButton(
            text="⬅️ Назад в меню",
            callback_data=build_cb(viewmodel.screen_id, "back")
        )]
    ])


async def build_admin_stats_keyboard(viewmodel: AdminStatsViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру экрана статистики"""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=build_cb(ScreenID.ADMIN_STATS, "refresh")
        )],
        [types.InlineKeyboardButton(
            text="⬅️ Назад в админ-панель",
            callback_data=build_cb(viewmodel.screen_id, "back")
        )]
    ])


async def build_admin_users_keyboard(viewmodel: AdminUsersViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру экрана списка пользователей с использованием Pagination"""
    from app.core.pagination import Pagination
    
    keyboard = []
    
    # Создаем Pagination объект из viewmodel
    pagination = Pagination(
        page=viewmodel.page,
        page_size=10,
        total=viewmodel.total
    )
    
    # Пагинация
    if pagination.total_pages > 1:
        nav_buttons = []
        if pagination.has_prev:
            # Создаем Pagination для предыдущей страницы (БЕЗ total - не передается в callback_data)
            prev_pagination = Pagination(
                page=pagination.prev_page(),
                page_size=pagination.page_size,
                total=0  # total не передается в payload
            )
            nav_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Предыдущая",
                callback_data=build_cb(ScreenID.ADMIN_USERS, "page", prev_pagination.to_payload())
            ))
        if pagination.has_next:
            # Создаем Pagination для следующей страницы (БЕЗ total - не передается в callback_data)
            next_pagination = Pagination(
                page=pagination.next_page(),
                page_size=pagination.page_size,
                total=0  # total не передается в payload
            )
            nav_buttons.append(types.InlineKeyboardButton(
                text="Следующая ➡️",
                callback_data=build_cb(ScreenID.ADMIN_USERS, "page", next_pagination.to_payload())
            ))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    keyboard.append([types.InlineKeyboardButton(
        text="⬅️ Назад в админ-панель",
        callback_data=build_cb(ScreenID.ADMIN_USERS, "back")
    )])
    
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


async def build_admin_payments_keyboard(viewmodel: AdminPaymentsViewModel) -> types.InlineKeyboardMarkup:
    """Строит клавиатуру экрана списка платежей с использованием Pagination"""
    from app.core.pagination import Pagination
    import json
    
    keyboard = []
    
    # Создаем Pagination объект из viewmodel
    pagination = Pagination(
        page=viewmodel.page,
        page_size=10,
        total=viewmodel.total
    )
    
    # Маппинг фильтра в строку (сжатый формат)
    filter_str = "all"
    if viewmodel.status_filter:
        filter_map = {
            None: "all",
            "succeeded": "suc",      # succeeded → suc
            "pending": "pen",         # pending → pen
            "canceled": "can",        # canceled → can
            "failed": "fail"          # failed → fail
        }
        filter_str = filter_map.get(viewmodel.status_filter, "all")
    
    # Пагинация
    if pagination.total_pages > 1:
        nav_buttons = []
        if pagination.has_prev:
            # Создаем payload с Pagination и фильтром (БЕЗ total, сжатые ключи)
            prev_pagination = Pagination(
                page=pagination.prev_page(),
                page_size=pagination.page_size,
                total=0  # total не передается в payload
            )
            # Используем компактный формат: p{page}s{page_size}f{filter}
            payload_str = f"{prev_pagination.to_payload()}f{filter_str}"
            nav_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Предыдущая",
                callback_data=build_cb(ScreenID.ADMIN_PAYMENTS, "page", payload_str)
            ))
        if pagination.has_next:
            # Создаем payload с Pagination и фильтром (БЕЗ total, сжатые ключи)
            next_pagination = Pagination(
                page=pagination.next_page(),
                page_size=pagination.page_size,
                total=0  # total не передается в payload
            )
            # Используем компактный формат: p{page}s{page_size}f{filter}
            payload_str = f"{next_pagination.to_payload()}f{filter_str}"
            nav_buttons.append(types.InlineKeyboardButton(
                text="Следующая ➡️",
                callback_data=build_cb(ScreenID.ADMIN_PAYMENTS, "page", payload_str)
            ))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    # Фильтры
    keyboard.extend([
        [types.InlineKeyboardButton(
            text="📊 Все",
            callback_data=build_cb(ScreenID.ADMIN_PAYMENTS, "filter", "all")
        )],
        [types.InlineKeyboardButton(
            text="✅ Успешные",
            callback_data=build_cb(ScreenID.ADMIN_PAYMENTS, "filter", "succeeded")
        )],
        [types.InlineKeyboardButton(
            text="⏳ Ожидают",
            callback_data=build_cb(ScreenID.ADMIN_PAYMENTS, "filter", "pending")
        )],
        [types.InlineKeyboardButton(
            text="⬅️ Назад в админ-панель",
            callback_data=build_cb(viewmodel.screen_id, "back")
        )]
    ])
    
    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)