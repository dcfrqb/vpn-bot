"""
Renderers для админских экранов
"""
from app.ui.viewmodels.admin import (
    AdminPanelViewModel,
    AdminStatsViewModel,
    AdminUsersViewModel,
    AdminPaymentsViewModel
)
from app.utils.html import escape_html


async def render_admin_panel(viewmodel: AdminPanelViewModel) -> str:
    """Рендерит главную панель администратора"""
    stats = viewmodel.stats
    return (
        "👑 <b>Панель администратора</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        "<blockquote>\n"
        f"• Пользователей: {stats['total_users']}\n"
        f"• Активных подписок: {stats['active_subscriptions']}\n"
        f"• Платежей: {stats['total_payments']}\n"
        f"• Доход: {stats['total_revenue']:.2f}₽\n"
        "</blockquote>\n\n"
        f"📈 <b>За сегодня:</b>\n"
        "<blockquote>\n"
        f"• Новых пользователей: {stats['today_users']}\n"
        f"• Платежей: {stats['today_payments']}\n"
        f"• Доход: {stats['today_revenue']:.2f}₽\n"
        "</blockquote>"
    )


async def render_admin_stats(viewmodel: AdminStatsViewModel) -> str:
    """Рендерит экран статистики"""
    stats = viewmodel.stats
    return (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 <b>Пользователи:</b> {stats['total_users']}\n"
        f"💳 <b>Платежи:</b> {stats['total_payments']}\n"
        f"🔄 <b>Активные подписки:</b> {stats['active_subscriptions']}\n"
        f"💰 <b>Доход:</b> {stats['total_revenue']:.2f}₽\n\n"
        f"📈 <b>За сегодня:</b>\n"
        f"• Новых пользователей: {stats['today_users']}\n"
        f"• Платежей: {stats['today_payments']}\n"
        f"• Доход: {stats['today_revenue']:.2f}₽"
    )


async def render_admin_users(viewmodel: AdminUsersViewModel) -> str:
    """Рендерит экран списка пользователей"""
    if not viewmodel.users:
        return (
            "👥 <b>Список пользователей</b>\n\n"
            "Пользователей пока нет."
        )
    
    text = f"👥 <b>Список пользователей</b>\n\n"
    text += f"Всего: {viewmodel.total}\n"
    text += f"Страница {viewmodel.page} из {viewmodel.total_pages}\n\n"
    
    for i, user in enumerate(viewmodel.users, 1):
        status = "✅ Активна" if user["has_active_subscription"] else "❌ Нет"
        plan = user["subscription_plan"] or "—"
        admin_badge = "👑 " if user["is_admin"] else ""
        username = user.get('username', 'не указан')
        text += (
            f"{i}. {admin_badge}@{escape_html(username)} (ID: {user['telegram_id']})\n"
            f"   Подписка: {status} ({escape_html(plan)})\n"
        )
    
    return text


async def render_admin_payments(viewmodel: AdminPaymentsViewModel) -> str:
    """Рендерит экран списка платежей"""
    if not viewmodel.payments:
        filter_text = {
            None: "Все",
            "succeeded": "Успешные",
            "pending": "Ожидающие",
            "canceled": "Отмененные",
            "failed": "Неудачные"
        }.get(viewmodel.status_filter, "Все")
        
        return (
            f"💳 <b>История платежей</b>\n\n"
            f"Фильтр: {filter_text}\n"
            f"Платежей не найдено."
        )
    
    filter_text = {
        None: "Все",
        "succeeded": "Успешные",
        "pending": "Ожидающие",
        "canceled": "Отмененные",
        "failed": "Неудачные"
    }.get(viewmodel.status_filter, "Все")
    
    text = f"💳 <b>История платежей</b>\n\n"
    text += f"Фильтр: {filter_text}\n"
    text += f"Всего: {viewmodel.total}\n"
    text += f"Страница {viewmodel.page} из {viewmodel.total_pages}\n\n"
    
    for i, payment in enumerate(viewmodel.payments, 1):
        status_emoji = {
            "succeeded": "✅",
            "pending": "⏳",
            "canceled": "❌",
            "failed": "⚠️"
        }.get(payment["status"], "❓")
        
        username = payment.get('username', 'не указан')
        text += (
            f"{i}. {status_emoji} {payment['amount']:.2f}{payment['currency']} - @{escape_html(username)}\n"
            f"   Статус: {payment['status']} | {payment['provider']}\n"
        )
    
    return text