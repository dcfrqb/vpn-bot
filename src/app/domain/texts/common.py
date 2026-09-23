"""Texts: Shared words: buttons Back/Close/Refresh, generic error text, support line.

Owner stream: D (User UI). Created empty by Foundation.
Plain module-level constants or small pure functions returning str.
Use helpers from app.domain.texts (h, plural_ru, fmt_date_msk, fmt_rub).
No letter U+0451 (yo) in prose.
"""

# Used by app.bot.middlewares.errors: never show exception text to users.
GENERIC_ERROR = "Что-то пошло не так. Попробуй еще раз чуть позже."
GENERIC_ERROR_ALERT = "Ошибка, попробуй еще раз"
MAINTENANCE = "Идут технические работы. Бот скоро вернется, данные и подписки на месте."

# --- Buttons shared across D screens ---
BTN_CONNECT = "🚀 Подключиться"
BTN_SUBSCRIPTION = "💳 Подписка"
BTN_REFRESH = "🔄 Обновить"
BTN_HELP = "ℹ️ Помощь"
BTN_DEVICES = "📱 Мои устройства"
BTN_ADMIN_PANEL = "👑 Админ-панель"
BTN_BACK_MAIN = "⬅️ В главное меню"
BTN_BACK = "⬅️ Назад"
BTN_SUPPORT = "✍️ Написать администратору"
BTN_OFFER = "📄 Оферта"
BTN_PRIVACY = "🔒 Политика конфиденциальности"
BTN_ARTICLE = "📖 Как подключиться"
BTN_TRIAL = "🎁 Попробовать 5 дней бесплатно"

REFRESHED = "Обновлено"

OFFER_URL = "https://telegra.ph/Publichnaya-oferta--CRS-VPN-04-08"

DEFAULT_SUPPORT_HANDLE = "dcfrq"


def support_url(handle: "str | None" = None) -> str:
    """t.me link to the support contact; falls back to the default handle."""
    h = (handle or DEFAULT_SUPPORT_HANDLE).lstrip("@")
    return f"https://t.me/{h}"


# --- Help screen (no dedicated text module in the plan; D owns support.py) ---
HELP_TEXT = (
    "ℹ️ <b>Справка по CRS VPN</b>\n\n"
    "🔐 <b>Что такое VPN?</b>\n"
    "<blockquote>"
    "VPN создает защищенное соединение между твоим устройством и интернетом."
    "</blockquote>\n\n"
    "❓ <b>Частые вопросы</b>\n"
    "<blockquote>"
    "<b>VPN не подключается:</b> обнови подписку (кнопка «Обновить» в меню) "
    "и проверь, что импортирована свежая ссылка.\n\n"
    "<b>Сколько устройств можно подключить:</b> смотри в разделе «Мои устройства», "
    "лимит зависит от тарифа.\n\n"
    "<b>Как сменить устройство:</b> отвяжи старое в «Мои устройства» и "
    "подключи новое той же ссылкой."
    "</blockquote>\n\n"
    "Не нашел ответ? Напиши в поддержку."
)

