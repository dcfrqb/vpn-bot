"""
Renderer для экрана профиля
"""
from app.ui.viewmodels.profile import ProfileViewModel
from app.utils.html import escape_html
from datetime import datetime


async def render_profile(viewmodel: ProfileViewModel) -> str:
    """Рендерит экран профиля"""
    text = "👤 <b>Ваш профиль</b>\n\n"
    
    # Основная информация
    text += f"🆔 <b>ID:</b> {viewmodel.user_id}\n"
    if viewmodel.username:
        text += f"👤 <b>Username:</b> @{escape_html(viewmodel.username)}\n"
    if viewmodel.created_at:
        text += f"📅 <b>Регистрация:</b> {viewmodel.created_at.strftime('%d.%m.%Y')}\n"
    
    text += "\n"
    
    # Информация о подписке
    if viewmodel.has_subscription:
        text += "✅ <b>Подписка:</b> Активна\n"
        if viewmodel.subscription_plan:
            text += f"💳 <b>Тариф:</b> {escape_html(viewmodel.subscription_plan)}\n"
        if viewmodel.subscription_valid_until:
            valid_until_str = viewmodel.subscription_valid_until.strftime("%d.%m.%Y %H:%M")
            text += f"📅 <b>Действует до:</b> {escape_html(valid_until_str)}\n"
        if viewmodel.subscription_days_left is not None and viewmodel.subscription_days_left > 0:
            text += f"⏰ <b>Осталось дней:</b> {viewmodel.subscription_days_left}\n"
    else:
        text += "❌ <b>Подписка:</b> Не активна\n"
    
    # Информация о платежах
    text += f"\n💳 <b>Платежи:</b>\n"
    text += f"• Всего успешных: {viewmodel.total_payments}\n"
    text += f"• Потрачено: {viewmodel.total_spent:.2f}₽\n"
    
    return text