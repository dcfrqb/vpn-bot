"""
Renderers для экранов подписки
"""
from app.ui.viewmodels.subscription import (
    SubscriptionViewModel,
    SubscriptionPlanDetailViewModel,
    SubscriptionPaymentViewModel
)
from app.utils.html import escape_html


async def render_subscription_plans(viewmodel: SubscriptionViewModel) -> str:
    """Рендерит экран выбора тарифов"""
    return (
        "💳 <b>Подписка на VPN</b>\n\n"
        "<b>Базовый</b>\n\n"
        "📋 <b>Включено:</b>\n"
        "<blockquote>"
        "• Неограниченный трафик и скорость\n"
        "• Поддержка разных устройств\n"
        "• YouTube без рекламы\n"
        "• Сервер NL\n"
        "• Подключение 5 устройств"
        "</blockquote>\n\n"
        "от 99 ₽/мес\n\n"
        "<b>Премиум</b>\n\n"
        "📋 <b>Включено:</b>\n"
        "<blockquote>"
        "• Неограниченный трафик и скорость\n"
        "• Поддержка разных устройств\n"
        "• YouTube без рекламы\n"
        "• Серверы NL, USA, FR\n"
        "• Подключение 15 устройств"
        "</blockquote>\n\n"
        "от 199 ₽/мес\n\n"
        "Выберите тариф:"
    )


async def render_subscription_plan_detail(viewmodel: SubscriptionPlanDetailViewModel) -> str:
    """Рендерит детальный экран тарифа"""
    text = f"💳 <b>{viewmodel.plan_name}</b>\n\n"
    
    # Если период выбран, показываем детали
    if viewmodel.period_months > 0:
        period_text = f"{viewmodel.period_months} месяц" if viewmodel.period_months == 1 else f"{viewmodel.period_months} месяцев"
        text += f"📅 <b>Период:</b> {period_text}\n"
        text += f"💰 <b>Стоимость:</b> {viewmodel.amount}₽\n\n"
    else:
        text += "📅 <b>Выберите период подписки:</b>\n\n"
    
    text += "📋 <b>Включено:</b>\n"
    text += "<blockquote>"
    
    for feature in viewmodel.features:
        text += f"• {escape_html(feature)}\n"
    
    text += "</blockquote>"
    
    if viewmodel.period_months > 0:
        text += "\n💡 Выберите способ оплаты:"
    else:
        text += "\n👇 Выберите период подписки ниже:"
    
    return text


async def render_subscription_payment(viewmodel: SubscriptionPaymentViewModel) -> str:
    """Рендерит экран оплаты"""
    period_text = f"{viewmodel.period_months} месяц" if viewmodel.period_months == 1 else f"{viewmodel.period_months} месяцев"
    
    text = f"💳 <b>Оплата подписки</b>\n\n"
    text += f"Тариф: {viewmodel.plan_name}\n"
    text += f"Период: {period_text}\n"
    text += f"Сумма: {viewmodel.amount}₽\n\n"
    
    if viewmodel.payment_url:
        text += "Нажмите кнопку ниже для оплаты:"
    elif viewmodel.crypto_address:
        text += f"Отправьте {viewmodel.amount}₽ на адрес:\n<code>{viewmodel.crypto_address}</code>"
    else:
        text += "Выберите способ оплаты:"
    
    return text