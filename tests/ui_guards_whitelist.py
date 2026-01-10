"""
Whitelist для исключений из архитектурных правил UI

Файлы и паттерны, где допустимы прямые вызовы .answer()/.edit_text()
но они должны быть минимальными и объяснены комментарием.
"""
from pathlib import Path

# Файлы, где допустимы прямые send для служебных/логических сообщений
WHITELIST_FILES = {
    # Payment notifications - служебные уведомления, не UI screens
    "src/app/routers/payments.py": [
        "notification",  # Уведомления о платежах
        "webhook",  # Webhook handlers
    ],
    # Crypto payment notifications
    "src/app/routers/crypto_payments.py": [
        "notification",  # Уведомления о крипто-платежах
        "admin_notification",  # Уведомления админу
    ],
    # Admin actions - не UI screens, а действия
    "src/app/routers/admin.py": [
        "admin_grant",  # Действия выдачи подписки
        "admin_reject",  # Действия отклонения запроса
    ],
}

# Паттерны в коде, которые допустимы (с комментарием "# UI EXCEPTION: ...")
ALLOWED_PATTERNS = [
    "callback.answer()",  # Без parse_mode - это OK
    "callback.answer(",  # Без parse_mode - это OK
    "ScreenManager",  # ScreenManager разрешен
    "screen_manager",  # screen_manager разрешен
    "show_screen",  # show_screen разрешен
    "handle_action",  # handle_action разрешен
    "navigate",  # navigate разрешен
]

# Комментарии, которые объясняют исключения
REQUIRED_COMMENT_PATTERN = "# UI EXCEPTION:"