"""
Renderers для экранов подписки.

Меню всегда показывает MENU_PLAN_CODES (lite/standard/pro). Имена/фичи —
из app.core.plans.PLAN_CATALOG. Если у юзера есть последний план,
сверху текста добавляется hint с предложением продлить.
"""
from app.core.plans import (
    MENU_PLAN_CODES,
    get_plan_features,
    get_plan_name,
)
from app.ui.viewmodels.subscription import (
    SubscriptionPaymentViewModel,
    SubscriptionPlanDetailViewModel,
    SubscriptionViewModel,
)
from app.utils.html import escape_html


# Эмодзи перед именем тарифа на экране выбора. Дефолт — 🌐.
_PLAN_EMOJI: dict[str, str] = {
    "basic": "🌐",
    "premium": "💎",
    "lite": "🟢",
    "standard": "🔵",
    "pro": "💎",
}


def _render_plan_block(plan_code: str) -> str:
    name = get_plan_name(plan_code)
    features = get_plan_features(plan_code)
    emoji = _PLAN_EMOJI.get(plan_code, "🌐")
    block = f"{emoji} <b>{escape_html(name)}</b>\n<blockquote>"
    block += "\n".join(f"• {escape_html(f)}" for f in features)
    block += "</blockquote>\n\n"
    return block


async def render_subscription_plans(viewmodel: SubscriptionViewModel) -> str:
    """Рендерит экран выбора тарифов — единое меню для всех."""
    text = "💳 <b>Подписка на VPN</b>\n\n"

    if viewmodel.last_plan_code:
        text += "🔄 Вы можете продлить текущую подписку кнопкой ниже.\n\n"

    for code in MENU_PLAN_CODES:
        text += _render_plan_block(code)
    text += "Выберите тариф:"
    return text


async def render_subscription_plan_detail(viewmodel: SubscriptionPlanDetailViewModel) -> str:
    """Рендерит детальный экран тарифа"""
    text = f"💳 <b>{escape_html(viewmodel.plan_name)}</b>\n\n"

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
        text += (
            "\n\n<i>Нажимая кнопку оплаты, вы принимаете условия "
            "<a href=\"https://telegra.ph/Publichnaya-oferta--CRS-VPN-04-08\">"
            "Публичной оферты</a> и <a href=\""
            "https://telegra.ph/Politika-konfidencialnosti--CRS-VPN-04-08\">"
            "Политики конфиденциальности</a>.</i>"
        )
    else:
        text += "\n👇 Выберите период подписки ниже:"

    return text


async def render_subscription_payment(viewmodel: SubscriptionPaymentViewModel) -> str:
    """Рендерит экран оплаты"""
    period_text = f"{viewmodel.period_months} месяц" if viewmodel.period_months == 1 else f"{viewmodel.period_months} месяцев"

    text = "💳 <b>Оплата подписки</b>\n\n"
    text += f"Тариф: {escape_html(viewmodel.plan_name)}\n"
    text += f"Период: {period_text}\n"
    text += f"Сумма: {viewmodel.amount}₽\n\n"

    if viewmodel.payment_url:
        text += "Нажмите кнопку ниже для оплаты:"
    else:
        text += "Выберите способ оплаты:"

    return text
