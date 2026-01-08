from aiogram import Router, types, F
from aiogram.filters import Command
from app.config import is_admin
from app.logger import logger
from app.services.stats import get_statistics, get_users_list, get_payments_list

router = Router(name="admin")

@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    """Панель администратора"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора")
        return
    
    logger.info(f"Администратор {message.from_user.id} открыл панель")
    
    stats = await get_statistics()
    
    await message.answer(
        f"👑 <b>Панель администратора</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Пользователей: {stats['total_users']}\n"
        f"• Активных подписок: {stats['active_subscriptions']}\n"
        f"• Платежей: {stats['total_payments']}\n"
        f"• Доход: {stats['total_revenue']:.2f}₽\n\n"
        f"📈 <b>За сегодня:</b>\n"
        f"• Новых пользователей: {stats['today_users']}\n"
        f"• Платежей: {stats['today_payments']}\n"
        f"• Доход: {stats['today_revenue']:.2f}₽",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [types.InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
            [types.InlineKeyboardButton(text="💳 Платежи", callback_data="admin_payments")],
            [types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main")]
        ])
    )

@router.message(Command("stats"))
async def admin_stats(message: types.Message):
    """Статистика для администратора"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора")
        return
    
    stats = await get_statistics()
    
    await message.answer(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 <b>Пользователи:</b> {stats['total_users']}\n"
        f"💳 <b>Платежи:</b> {stats['total_payments']}\n"
        f"🔄 <b>Активные подписки:</b> {stats['active_subscriptions']}\n"
        f"💰 <b>Доход:</b> {stats['total_revenue']:.2f}₽\n\n"
        f"📈 <b>За сегодня:</b>\n"
        f"• Новых пользователей: {stats['today_users']}\n"
        f"• Платежей: {stats['today_payments']}\n"
        f"• Доход: {stats['today_revenue']:.2f}₽"
    )

@router.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: types.CallbackQuery):
    """Статистика через callback"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    await callback.answer()  # Быстрый ответ
    stats = await get_statistics()
    
    await callback.message.edit_text(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 <b>Пользователи:</b> {stats['total_users']}\n"
        f"💳 <b>Платежи:</b> {stats['total_payments']}\n"
        f"🔄 <b>Активные подписки:</b> {stats['active_subscriptions']}\n"
        f"💰 <b>Доход:</b> {stats['total_revenue']:.2f}₽\n\n"
        f"📈 <b>За сегодня:</b>\n"
        f"• Новых пользователей: {stats['today_users']}\n"
        f"• Платежей: {stats['today_payments']}\n"
        f"• Доход: {stats['today_revenue']:.2f}₽",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats")],
            [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
        ])
    )


@router.callback_query(F.data == "admin_users")
async def admin_users_callback(callback: types.CallbackQuery):
    """Список пользователей"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    await callback.answer()  # Быстрый ответ
    
    users_data = await get_users_list(page=1, page_size=10)
    
    if not users_data["users"]:
        await callback.message.edit_text(
            "👥 <b>Список пользователей</b>\n\n"
            "Пользователей пока нет.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
            ])
        )
        return
    
    text = f"👥 <b>Список пользователей</b>\n\n"
    text += f"Всего: {users_data['total']}\n"
    text += f"Страница {users_data['page']} из {users_data['total_pages']}\n\n"
    
    for i, user in enumerate(users_data["users"], 1):
        status = "✅ Активна" if user["has_active_subscription"] else "❌ Нет"
        plan = user["subscription_plan"] or "—"
        admin_badge = "👑 " if user["is_admin"] else ""
        text += (
            f"{i}. {admin_badge}@{user['username']} (ID: {user['telegram_id']})\n"
            f"   Подписка: {status} ({plan})\n"
        )
    
    keyboard = []
    if users_data["total_pages"] > 1:
        nav_buttons = []
        if users_data["page"] > 1:
            nav_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Предыдущая", callback_data=f"admin_users_page_{users_data['page'] - 1}"
            ))
        if users_data["page"] < users_data["total_pages"]:
            nav_buttons.append(types.InlineKeyboardButton(
                text="Следующая ➡️", callback_data=f"admin_users_page_{users_data['page'] + 1}"
            ))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    keyboard.append([types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.callback_query(F.data.startswith("admin_users_page_"))
async def admin_users_page_callback(callback: types.CallbackQuery):
    """Пагинация списка пользователей"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    page = int(callback.data.split("_")[-1])
    await callback.answer()  # Быстрый ответ
    
    users_data = await get_users_list(page=page, page_size=10)
    
    if not users_data["users"]:
        await callback.message.edit_text(
            "👥 <b>Список пользователей</b>\n\n"
            "На этой странице нет пользователей.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
            ])
        )
        return
    
    text = f"👥 <b>Список пользователей</b>\n\n"
    text += f"Всего: {users_data['total']}\n"
    text += f"Страница {users_data['page']} из {users_data['total_pages']}\n\n"
    
    for i, user in enumerate(users_data["users"], 1):
        status = "✅ Активна" if user["has_active_subscription"] else "❌ Нет"
        plan = user["subscription_plan"] or "—"
        admin_badge = "👑 " if user["is_admin"] else ""
        text += (
            f"{i}. {admin_badge}@{user['username']} (ID: {user['telegram_id']})\n"
            f"   Подписка: {status} ({plan})\n"
        )
    
    keyboard = []
    if users_data["total_pages"] > 1:
        nav_buttons = []
        if users_data["page"] > 1:
            nav_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Предыдущая", callback_data=f"admin_users_page_{users_data['page'] - 1}"
            ))
        if users_data["page"] < users_data["total_pages"]:
            nav_buttons.append(types.InlineKeyboardButton(
                text="Следующая ➡️", callback_data=f"admin_users_page_{users_data['page'] + 1}"
            ))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    keyboard.append([types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.callback_query(F.data == "admin_payments")
async def admin_payments_callback(callback: types.CallbackQuery):
    """Список платежей"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    await callback.answer()  # Быстрый ответ
    
    payments_data = await get_payments_list(page=1, page_size=10)
    
    if not payments_data["payments"]:
        await callback.message.edit_text(
            "💳 <b>История платежей</b>\n\n"
            "Платежей пока нет.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📊 Все", callback_data="admin_payments_all")],
                [types.InlineKeyboardButton(text="✅ Успешные", callback_data="admin_payments_succeeded")],
                [types.InlineKeyboardButton(text="⏳ Ожидают", callback_data="admin_payments_pending")],
                [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
            ])
        )
        return
    
    text = f"💳 <b>История платежей</b>\n\n"
    text += f"Всего: {payments_data['total']}\n"
    text += f"Страница {payments_data['page']} из {payments_data['total_pages']}\n\n"
    
    for i, payment in enumerate(payments_data["payments"], 1):
        status_emoji = {
            "succeeded": "✅",
            "pending": "⏳",
            "canceled": "❌",
            "failed": "⚠️"
        }.get(payment["status"], "❓")
        
        text += (
            f"{i}. {status_emoji} {payment['amount']:.2f}{payment['currency']} - @{payment['username']}\n"
            f"   Статус: {payment['status']} | {payment['provider']}\n"
        )
    
    keyboard = []
    if payments_data["total_pages"] > 1:
        nav_buttons = []
        if payments_data["page"] > 1:
            nav_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Предыдущая", callback_data=f"admin_payments_page_{payments_data['page'] - 1}"
            ))
        if payments_data["page"] < payments_data["total_pages"]:
            nav_buttons.append(types.InlineKeyboardButton(
                text="Следующая ➡️", callback_data=f"admin_payments_page_{payments_data['page'] + 1}"
            ))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="📊 Все", callback_data="admin_payments_all")],
        [types.InlineKeyboardButton(text="✅ Успешные", callback_data="admin_payments_succeeded")],
        [types.InlineKeyboardButton(text="⏳ Ожидают", callback_data="admin_payments_pending")],
        [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.callback_query(F.data.startswith("admin_payments_"))
async def admin_payments_filter_callback(callback: types.CallbackQuery):
    """Фильтрация и пагинация платежей"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    data_parts = callback.data.split("_")
    
    if len(data_parts) >= 4 and data_parts[2] == "page":
        page = int(data_parts[3])
        if len(data_parts) > 4:
            status_str = data_parts[4]
            status_map = {
                "all": None,
                "succeeded": "succeeded",
                "pending": "pending",
                "canceled": "canceled",
                "failed": "failed"
            }
            status = status_map.get(status_str, None)
        else:
            status = None
        await callback.answer()  # Быстрый ответ
    else:
        filter_type = data_parts[2]
        status_map = {
            "all": None,
            "succeeded": "succeeded",
            "pending": "pending",
            "canceled": "canceled",
            "failed": "failed"
        }
        status = status_map.get(filter_type)
        page = 1
        await callback.answer()  # Быстрый ответ
    
    payments_data = await get_payments_list(page=page, page_size=10, status=status)
    
    if not payments_data["payments"]:
        filter_text = {
            None: "Все",
            "succeeded": "Успешные",
            "pending": "Ожидающие",
            "canceled": "Отмененные",
            "failed": "Неудачные"
        }.get(status, "Все")
        
        await callback.message.edit_text(
            f"💳 <b>История платежей</b>\n\n"
            f"Фильтр: {filter_text}\n"
            f"Платежей не найдено.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📊 Все", callback_data="admin_payments_all")],
                [types.InlineKeyboardButton(text="✅ Успешные", callback_data="admin_payments_succeeded")],
                [types.InlineKeyboardButton(text="⏳ Ожидают", callback_data="admin_payments_pending")],
                [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
            ])
        )
        return
    
    filter_text = {
        None: "Все",
        "succeeded": "Успешные",
        "pending": "Ожидающие",
        "canceled": "Отмененные",
        "failed": "Неудачные"
    }.get(status, "Все")
    
    text = f"💳 <b>История платежей</b>\n\n"
    text += f"Фильтр: {filter_text}\n"
    text += f"Всего: {payments_data['total']}\n"
    text += f"Страница {payments_data['page']} из {payments_data['total_pages']}\n\n"
    
    for i, payment in enumerate(payments_data["payments"], 1):
        status_emoji = {
            "succeeded": "✅",
            "pending": "⏳",
            "canceled": "❌",
            "failed": "⚠️"
        }.get(payment["status"], "❓")
        
        text += (
            f"{i}. {status_emoji} {payment['amount']:.2f}{payment['currency']} - @{payment['username']}\n"
            f"   Статус: {payment['status']} | {payment['provider']}\n"
        )
    
    keyboard = []
    if payments_data["total_pages"] > 1:
        nav_buttons = []
        if payments_data["page"] > 1:
            nav_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Предыдущая", 
                callback_data=f"admin_payments_page_{payments_data['page'] - 1}_{status or 'all'}"
            ))
        if payments_data["page"] < payments_data["total_pages"]:
            nav_buttons.append(types.InlineKeyboardButton(
                text="Следующая ➡️", 
                callback_data=f"admin_payments_page_{payments_data['page'] + 1}_{status or 'all'}"
            ))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="📊 Все", callback_data="admin_payments_all")],
        [types.InlineKeyboardButton(text="✅ Успешные", callback_data="admin_payments_succeeded")],
        [types.InlineKeyboardButton(text="⏳ Ожидают", callback_data="admin_payments_pending")],
        [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.callback_query(F.data == "admin_back")
async def admin_back_callback(callback: types.CallbackQuery):
    """Возврат в админ-панель"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    await callback.answer()
    
    stats = await get_statistics()
    
    await callback.message.edit_text(
        f"👑 <b>Панель администратора</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Пользователей: {stats['total_users']}\n"
        f"• Активных подписок: {stats['active_subscriptions']}\n"
        f"• Платежей: {stats['total_payments']}\n"
        f"• Доход: {stats['total_revenue']:.2f}₽\n\n"
        f"📈 <b>За сегодня:</b>\n"
        f"• Новых пользователей: {stats['today_users']}\n"
        f"• Платежей: {stats['today_payments']}\n"
        f"• Доход: {stats['today_revenue']:.2f}₽",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [types.InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
            [types.InlineKeyboardButton(text="💳 Платежи", callback_data="admin_payments")],
            [types.InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_main")]
        ])
    )
