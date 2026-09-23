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
