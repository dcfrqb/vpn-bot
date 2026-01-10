"""
Renderer для экранов подключения
"""
from typing import Optional
from app.utils.html import escape_html


async def render_connect_loading() -> str:
    """Рендерит экран загрузки при получении ссылки"""
    return (
        "⏳ <b>Получение ссылки подписки</b>\n\n"
        "Обрабатываем запрос..."
    )


async def render_connect_success(subscription_url: str) -> str:
    """Рендерит экран успешного получения ссылки"""
    return (
        "🚀 <b>Ссылка для подключения VPN</b>\n\n"
        "Используйте эту ссылку для настройки VPN на вашем устройстве:\n\n"
        f"<code>{escape_html(subscription_url)}</code>\n\n"
        "💡 <b>Как использовать:</b>\n\n"
        "<b>Вариант 1:</b>\n"
        "<blockquote>\n"
        "1. Откройте ссылку\n"
        "2. Скачайте подходящий VPN клиент\n"
        "3. Импортируйте подписку\n"
        "</blockquote>\n\n"
        "<b>Вариант 2:</b>\n"
        "<blockquote>\n"
        "1. Скопируйте ссылку подписки\n"
        "2. Вставьте ее в VPN клиент\n"
        "</blockquote>"
    )


async def render_connect_error(error_message: Optional[str] = None) -> str:
    """Рендерит экран ошибки подключения"""
    message = error_message or "Не удалось получить ссылку"
    return (
        f"❌ <b>Ошибка подключения</b>\n\n"
        f"{escape_html(message)}\n\n"
        "Пожалуйста, попробуйте позже или обратитесь в поддержку: @dcfrq"
    )


async def render_connect_no_subscription() -> str:
    """Рендерит экран отсутствия подписки"""
    return (
        "❌ <b>Ваша подписка неактивна</b>\n\n"
        "Для подключения необходимо:\n"
        "— оформить подписку\n"
        "— или обратиться к администратору"
    )