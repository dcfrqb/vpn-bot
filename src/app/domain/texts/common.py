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
BTN_SUPPORT = "✍️ Поддержка"
BTN_OFFER = "📄 Оферта"
BTN_PRIVACY = "🔒 Политика конфиденциальности"
BTN_ARTICLE = "📖 Инструкция"  # the article URL; "🚀 Подключиться" opens the connect screen
BTN_TRIAL = "🎁 Попробовать 5 дней бесплатно"
# One vocabulary for every screen and push (review UX M4): money, promo and
# notify texts import these instead of their own wording.
BTN_PAY_PREFIX = "💳 Оплатить"
BTN_CHECK_PAYMENT = "🔄 Проверить оплату"

REFRESHED = "Обновлено"

OFFER_URL = "https://telegra.ph/Publichnaya-oferta--CRS-VPN-04-08"

DEFAULT_SUPPORT_HANDLE = "dcfrq"


def support_handle(settings) -> "str | None":
    """Support contact: SUPPORT_HANDLE, else ADMIN_SUPPORT_USERNAME (review UX m14)."""
    handle = getattr(settings, "SUPPORT_HANDLE", None) or getattr(settings, "ADMIN_SUPPORT_USERNAME", None)
    return str(handle).strip() if handle else None


def support_url(handle: "str | None" = None) -> str:
    """t.me link to the support contact; falls back to the default handle."""
    h = (handle or DEFAULT_SUPPORT_HANDLE).lstrip("@")
    return f"https://t.me/{h}"


# --- Help screen (no dedicated text module in the plan; D owns support.py) ---
def help_text(unlink_enabled: bool = False) -> str:
    """FAQ. Unlinking is promised only when DEVICES_UNLINK_ENABLED is on
    (review UX M3); the refresh tip points at the VPN app, not the bot."""
    change_device = (
        "<b>Как сменить устройство:</b> отвяжи старое в «Мои устройства» и "
        "подключи новое той же ссылкой."
        if unlink_enabled else
        "<b>Как сменить устройство:</b> напиши в поддержку, освободим место под новое, "
        "и подключи его той же ссылкой."
    )
    return (
        "ℹ️ <b>Справка по CRS VPN</b>\n\n"
        "🔐 <b>Что такое VPN?</b>\n"
        "<blockquote>"
        "VPN создает защищенное соединение между твоим устройством и интернетом."
        "</blockquote>\n\n"
        "❓ <b>Частые вопросы</b>\n"
        "<blockquote>"
        "<b>VPN не подключается:</b> обнови подписку в самом приложении (кнопка обновления "
        "или свайп вниз по списку серверов) и проверь, что добавлена ссылка из «Подключиться».\n\n"
        "<b>Сколько устройств можно подключить:</b> смотри в разделе «Мои устройства», "
        "лимит зависит от тарифа.\n\n"
        f"{change_device}"
        "</blockquote>\n\n"
        "Не нашел ответ? Напиши в поддержку."
    )


HELP_TEXT = help_text(False)


# --- fallback router (unknown or retired buttons) and small commands ---
STALE_BUTTON = "Эта кнопка устарела, открыл главное меню"


def myid_text(user_id: int, is_admin: bool) -> str:
    text = f"🆔 <b>Твой Telegram ID:</b> <code>{int(user_id)}</code>"
    if is_admin:
        text += "\n\n✅ Статус: администратор"
    return text
