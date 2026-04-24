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
        "прямой вызов",  # Прямые вызовы UI методов с комментариями
        "быстрый ответ",  # Быстрые ответы на callback
        "ошибка",  # Ошибки и исключения
        "импорт",  # Импорты для передачи в ScreenManager
    ],
    # Admin actions - не UI screens, а действия
    "src/app/routers/admin.py": [
        "admin_grant",  # Действия выдачи подписки
        "admin_grant_forever",  # Выдача премиум навсегда
        "admin_reject",  # Действия отклонения запроса
        "прямой вызов",  # Прямые вызовы UI методов с комментариями
        "быстрый ответ",  # Быстрые ответы на callback
        "ошибка",  # Ошибки и исключения
        "администратор",  # Действия администратора
        "импорт",  # Импорты для передачи в ScreenManager
    ],
    # Start handlers - legacy handlers, будут переведены на ScreenManager
    "src/app/routers/start.py": [
        "прямой вызов",  # Прямые вызовы UI методов с комментариями
        "быстрый ответ",  # Быстрые ответы на callback
        "ошибка",  # Ошибки и исключения
        "legacy handler",  # Legacy handlers, будут переведены
        "обработка ошибки",  # Обработка ошибок
        "импорт",  # Импорты для передачи в ScreenManager
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