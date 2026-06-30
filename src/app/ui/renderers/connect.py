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


def _fmt_gb(num_bytes: Optional[int]) -> str:
    """Форматирует байты в ГБ для показа остатка обхода."""
    if num_bytes is None:
        return "—"
    gb = num_bytes / (1024 * 1024 * 1024)
    if gb >= 10:
        return f"{gb:.0f} ГБ"
    return f"{gb:.1f} ГБ"


def _render_obhod_block(
    is_pro: bool,
    obhod_url: Optional[str],
    obhod_used_bytes: Optional[int],
    obhod_limit_bytes: Optional[int],
    obhod_active: bool,
) -> str:
    """Блок обхода под основной ссылкой.

    Pro с активным обходом — ссылка обхода + остаток + короткая подсказка.
    Не-Pro — заглушка «Обход доступен в Pro».
    """
    if not is_pro:
        return (
            "\n\n———\n"
            "🛡 <b>Обход блокировок</b>\n"
            "Доступен в тарифе Pro. Отдельная ссылка для сайтов, "
            "которые заблокированы."
        )

    if not obhod_active or not obhod_url:
        return (
            "\n\n———\n"
            "🛡 <b>Обход блокировок</b>\n"
            "Готовим вашу ссылку обхода. Загляните чуть позже или "
            "нажмите «Обновить»."
        )

    return (
        "\n\n———\n"
        "🛡 <b>Обход блокировок</b>\n"
        "<blockquote>"
        "Отдельная ссылка. Добавляется так же, как и первая. "
        "Включайте обход, когда мобильный интернет отключен, "
        "и выключайте, когда все работает штатно."
        "</blockquote>\n\n"
        f"<code>{escape_html(obhod_url)}</code>"
    )


async def render_connect_success(subscription_url: str) -> str:
    """Рендерит экран успешного получения ссылки (только основная)."""
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


async def render_connect_success_with_obhod(viewmodel) -> str:
    """Экран «Подключиться» с основной ссылкой и блоком обхода (один экран)."""
    base = await render_connect_success(viewmodel.subscription_url)
    return base + _render_obhod_block(
        is_pro=viewmodel.is_pro,
        obhod_url=viewmodel.obhod_url,
        obhod_used_bytes=viewmodel.obhod_used_bytes,
        obhod_limit_bytes=viewmodel.obhod_limit_bytes,
        obhod_active=viewmodel.obhod_active,
    )


async def render_connect_error(error_message: Optional[str] = None) -> str:
    """Рендерит экран ошибки подключения"""
    return (
        "❌ <b>Не удалось получить ссылку подключения</b>\n\n"
        "Сервис временно недоступен. Попробуйте нажать «Обновить» или зайдите позже.\n\n"
        "Если проблема не исчезает — обратитесь в поддержку: @dcfrq"
    )


async def render_connect_no_subscription() -> str:
    """Рендерит экран отсутствия подписки"""
    return (
        "🔒 <b>Подписка не активна</b>\n\n"
        "Для подключения к VPN нужна активная подписка.\n\n"
        "Нажмите кнопку ниже, чтобы выбрать тариф и оформить доступ."
    )