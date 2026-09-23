"""Texts: Admin panel, broadcasts, promo admin texts.

Owner stream: E (Growth & admin).
Plain module-level constants or small pure functions returning str.
Use helpers from app.domain.texts (h, plural_ru, fmt_date_msk, fmt_rub).
No letter U+0451 (yo) in prose. Screen builders over service DTOs live in
app.bot.views.admin / app.bot.views.broadcast.
"""
from __future__ import annotations

# ----------------------------------------------------------------- panel buttons

BTN_STATS = "📊 Статистика"
BTN_USERS = "👥 Пользователи"
BTN_PAYMENTS = "💳 Платежи"
BTN_PROMO = "🎟 Промокоды"
BTN_BROADCASTS = "📢 Рассылки"
BTN_OBHOD = "🛡 Обход"
BTN_BLOCKLIST = "⛔ Стоп-лист"
BTN_REFERRAL = "🤝 Рефералка"
BTN_BACK = "⬅️ Назад"
BTN_PANEL = "⬅️ В админку"
BTN_PREV = "⬅️"
BTN_NEXT = "➡️"
BTN_REFRESH = "🔄 Обновить"

PANEL_TITLE = "🛠 <b>Админ-панель</b>"
NO_DB = "База данных не настроена."
DONE = "Готово"
ALREADY_DONE = "Запрос уже обработан"
IN_PROGRESS = "Этому пользователю уже выдают доступ"
GRANT_FAILED = "Выдача не удалась, подробности в логах"
NOTHING_TO_EXTEND = "Продлевать нечего: у пользователя нет подписки (укажи тариф)"
USE_PANEL = "Такие запросы обрабатываются вручную в Remnawave"
PROCESSED = "✅ Обработано"

# ----------------------------------------------------------------- requests (/friend, /admin)

REQUEST_TITLE_FRIEND = "👤 <b>Запрос на доступ (/friend)</b>"
REQUEST_TITLE_ADMIN = "👤 <b>Запрос на доступ (промокод /admin)</b>"
REQUEST_HINT = "Выдай Pro или отклони запрос."
BTN_GRANT_1M = "Выдать Pro на 1 месяц"
BTN_GRANT_3M = "Выдать Pro на 3 месяца"
BTN_GRANT_FOREVER = "Выдать Pro навсегда"
BTN_REJECT = "Отклонить"

# ----------------------------------------------------------------- usage hints

USAGE_GRANT = "Использование: <code>/grant &lt;telegram_id&gt; &lt;дней&gt; [тариф]</code>"
USAGE_ID = "Использование: <code>/{cmd} &lt;telegram_id&gt;</code>"
USAGE_PAYOUT = (
    "Использование: <code>/referral_payout sun718 &lt;месяцев&gt; [комментарий]</code>\n"
    "Пример: <code>/referral_payout sun718 3 продлил в панели</code>"
)
USAGE_PROMO_NEW = (
    "Новый промокод одной строкой:\n"
    "<code>/promo_new КОД days=7 [plan=standard] [kind=days|plan] [audience=new|existing|any] "
    "[max=100] [per_user=1] [valid=30] [traffic=0] [devices=0]</code>\n\n"
    "• days: сколько дней дает код\n"
    "• kind=days продлевает текущий тариф (или plan, если подписки нет); kind=plan выдает plan\n"
    "• audience: new (не платили и без подписки), existing (платили или есть подписка), any\n"
    "• max: всего активаций, per_user: на одного человека, valid: дней до окончания\n"
    "• traffic (ГБ) и devices передаются в выдачу как лимиты"
)
USAGE_STOPLIST = (
    "Стоп-лист (кому не продаем):\n"
    "<code>/stoplist_add &lt;telegram_id | карта&gt; [причина]</code>\n"
    "<code>/stoplist_del &lt;telegram_id | карта&gt;</code>\n"
    "Карта: <code>220220-7882-09/2028</code> (first6-last4-MM/YYYY).\n\n"
    "Полная блокировка в боте: <code>/block &lt;id&gt;</code>, <code>/unblock &lt;id&gt;</code>"
)

# ----------------------------------------------------------------- broadcast wizard

BC_STEP_TEXT = (
    "📢 <b>Новая рассылка, шаг 1/6: текст</b>\n\n"
    "Пришли текст (HTML: &lt;b&gt;, &lt;i&gt;, &lt;a&gt;, &lt;code&gt;, &lt;blockquote&gt;). Отмена: /cancel"
)
BC_STEP_PHOTO = "📷 <b>Шаг 2/6: фото</b>\n\nПришли фото или нажми «Без фото»."
BC_STEP_BUTTONS = (
    "🔘 <b>Шаг 3/6: кнопки</b>\n\nJSON-массив или «Без кнопок». Пример:\n"
    "<code>[{\"text\": \"Открыть сайт\", \"url\": \"https://example.com\"}]</code>\n"
    "Поля: text + url или text + callback_data. «Отписаться» и «Закрыть» добавятся сами."
)
BC_STEP_SEGMENT = "👥 <b>Шаг 4/6: кому</b>"
BC_STEP_DAYS = (
    "Пришли число дней N для «в пределах N дней» (истекает в ближайшие N дней для активных, "
    "истекла за последние N дней для истекших, триал за последние N дней) или 0, чтобы без ограничения."
)
BC_STEP_IDS = "Пришли Telegram ID через пробел, запятую или с новой строки (до 1000)."
BC_STEP_CREDIT = "🎁 <b>Шаг 5/6: подарок</b>\n\nСколько дней начислить каждому, кому сообщение дошло?"
BC_STEP_SOUND = "🔔 <b>Шаг 6/6: звук</b>"
BC_CANCELLED = "Создание рассылки отменено."
BC_EMPTY_TEXT = "Пустой текст, пришли непустое сообщение."
BC_TOO_LONG = "Слишком длинный текст ({n} симв.), лимит Telegram около 4096."
BC_BAD_BUTTONS = "Не получилось прочитать кнопки: {err}. Попробуй еще раз или /cancel."
BC_BAD_NUMBER = "Нужно целое число от 0 до {max}."
BC_BAD_IDS = "Не нашел ни одного ID. Пришли числа через пробел."
BC_NOT_FOUND = "Рассылка не найдена."
BC_ALREADY_RUNNING = "Рассылка уже запущена."
BC_ALREADY_DONE = "Рассылка уже завершена."
BC_STARTED = "🚀 Рассылка запущена."
BC_CANCEL_SENT = "🛑 Остановка отправлена. Сообщения в пути дойдут."
BC_NOT_RUNNING = "Рассылка не запущена."
BC_DELETED = "Черновик удален."
BC_PREVIEW_FAILED = "Превью не отправилось: {err}"

SEGMENT_TITLES = {
    "all": "все",
    "active": "с активной подпиской",
    "expired": "подписка истекла",
    "never": "никогда не платили",
    "trial_nc": "брали триал, не купили",
    "ids": "список ID",
}

# ----------------------------------------------------------------- user-side (/stop)

STOP_DONE = (
    "🔕 Ты отписан(а) от рассылок.\n\n"
    "Уведомления об оплате и подписке продолжат приходить. Подписаться снова: /start."
)
UNSUB_ALERT = "🔕 Ты отписан(а) от рассылок"
