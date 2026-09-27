"""Texts: Admin panel, broadcasts, promo admin texts.

Owner stream: E (Growth & admin).
Plain module-level constants or small pure functions returning str.
Use helpers from app.domain.texts (h, plural_ru, fmt_date_msk, fmt_rub).
No letter U+0451 (yo) in prose. Screen builders (``*_screen`` -> ``ui.Screen``)
live here (release 3.0, screen kit); ``app.bot.views.admin`` /
``app.bot.views.broadcast`` turn them into views with ``kit.view(...)``.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from app.domain.texts import fmt_date_msk, fmt_gb, fmt_rub, h
from app.domain.texts import ui
from app.domain.texts.ui import E, block, field

# ----------------------------------------------------------------- panel buttons

BTN_STATS = "📊 Статистика"
BTN_USERS = "👥 Пользователи"
BTN_PAYMENTS = "💳 Платежи"
BTN_PROMO = "🎟 Промокоды"
BTN_BROADCASTS = "📢 Рассылки"
BTN_OBHOD = "🛡 Обход"
BTN_BLOCKLIST = "⛔ Стоп-лист"
BTN_REFERRAL = "🤝 Рефералка"
BTN_MAINT = "🛠 Техработы"
BTN_BACK = "⬅️ Назад"
BTN_PANEL = "👑 В админку"
BTN_PREV = "⬅️"
BTN_NEXT = "➡️"
BTN_REFRESH = "🔄 Обновить"

PANEL_TITLE = "🛠 <b>Админ-панель</b>"
NO_DB = "База данных не настроена."
DONE = "Готово"
ALREADY_DONE = "Запрос уже обработан"
IN_PROGRESS = "Этому пользователю уже выдают доступ"
GRANT_FAILED = "Выдача не удалась, подробности в логах"
GRANT_UNKNOWN = (
    "⚠️ Панель не ответила, выдача могла пройти. Проверь срок в панели. "
    "Повторить ту же команду безопасно: дни не начислятся дважды."
)
NOTHING_TO_EXTEND = "Продлевать нечего: у пользователя нет подписки (укажи тариф)"
USE_PANEL = "Такие запросы обрабатываются вручную в Remnawave"
PROCESSED = "✅ Обработано"

# ----------------------------------------------------------------- requests (/friend, /admin)

REQUEST_TITLE_FRIEND = "Запрос на доступ (/friend)"
REQUEST_TITLE_ADMIN = "Запрос на доступ (промокод /admin)"
REQUEST_HINT = "Выдай Pro или отклони запрос."


def access_request_alert(title: str, *, name: str, username: "str | None", telegram_id: int) -> str:
    """/friend and /admin (non-admin) access requests: ``ui.admin_alert``, HTML."""
    from app.domain.texts import ui

    return ui.admin_alert(title, emoji="👤", who=ui.who_block(name=name, username=username, telegram_id=telegram_id),
                          hint=REQUEST_HINT).html()
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
    "Пришли текст (HTML: &lt;b&gt;, &lt;i&gt;, &lt;a&gt;, &lt;code&gt;, &lt;blockquote&gt;)."
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
BC_BAD_BUTTONS = "Не получилось прочитать кнопки: {err}. Пришли еще раз или нажми «Без кнопок»."
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

def _stop_done() -> str:
    from app.domain.texts import ui

    return ui.result("ok", "Ты отписан(а) от рассылок",
                     "Уведомления об оплате и подписке продолжат приходить.",
                     hint="Подписаться снова: /start").html()


STOP_DONE = _stop_done()
UNSUB_ALERT = "🔕 Ты отписан(а) от рассылок"

# ----------------------------------------------------------------- screens (release 3.0, kit)


def panel_screen(stats: Any) -> ui.Screen:
    return ui.admin_screen(
        "Админ-панель", emoji="🛠",
        lines=[
            f"👥 Пользователей: {stats.total_users} (сегодня +{stats.today_users})",
            f"🔄 Активных подписок: {stats.active_subscriptions}",
            f"🎁 Триалов: {stats.trials_total} (сегодня {stats.trials_today})",
            f"💰 Выручка сегодня: {fmt_rub(stats.revenue_today)}, за 30 дней: {fmt_rub(stats.revenue_30d)}",
        ],
    )


def stats_screen(s: Any) -> ui.Screen:
    return ui.admin_screen(
        "Статистика", emoji="📊",
        hint="Основные подписки, без тестовых и промо-платежей",
        sections=[
            block(field("Пользователи", s.total_users), f"сегодня +{s.today_users}",
                  field("Активные подписки", s.active_subscriptions),
                  f"Триалы: {s.trials_total}, сегодня {s.trials_today}"),
            block(field("Оплат", s.paid_total), f"сегодня {s.paid_today}",
                  field("Выручка", fmt_rub(s.revenue_total)),
                  f"· сегодня: {fmt_rub(s.revenue_today)}",
                  f"· за 30 дней: {fmt_rub(s.revenue_30d)}",
                  field("Возвращено", fmt_rub(s.refunded_total))),
        ],
    )


def users_screen(data: dict) -> ui.Screen:
    total_pages = max(1, int(data.get("total_pages") or 1))
    rows = data.get("users") or []
    lines = []
    for u in rows:
        name = u.get("username") or u.get("first_name") or "—"
        plan = u.get("subscription_plan") or "—"
        lines.append(f"• <code>{h(u.get('telegram_id'))}</code> {h(name)} · {h(plan)}")
    return ui.admin_screen(
        "Пользователи", emoji="👥",
        hint=f"Всего {data.get('total', 0)}, стр. {data.get('page', 1)} из {total_pages}",
        lines=lines or ["Пусто."],
    )


PAYMENT_FILTERS = {"all": "Все", "succeeded": "Успешные", "pending": "Ожидают", "canceled": "Отмененные",
                   "failed": "Неудачные"}
_STATUS_ICON = {"succeeded": "✅", "pending": "⏳", "canceled": "❌", "failed": "⚠️"}


def payments_screen(data: dict, flt: str) -> ui.Screen:
    flt = flt if flt in PAYMENT_FILTERS else "all"
    total_pages = max(1, int(data.get("total_pages") or 1))
    lines = []
    for i, p in enumerate(data.get("payments") or [], 1):
        icon = _STATUS_ICON.get(p.get("status"), "❓")
        who = f"@{h(p.get('username'))}" if p.get("username") else f"<code>{h(p.get('telegram_id') or '—')}</code>"
        lines.append(f"{i}. {icon} {fmt_rub(p.get('amount'))} · {who} · {h(p.get('provider'))}")
    return ui.admin_screen(
        f"Платежи · {PAYMENT_FILTERS[flt]}", emoji="💳",
        hint=f"Всего: {data.get('total', 0)}, стр. {data.get('page', 1)} из {total_pages}",
        lines=lines or ["Платежей не найдено."],
    )


def whois_screen(card: Any, state: Any, *, bot_blocked: bool, is_admin: bool) -> ui.Screen:
    lines = []
    if card.known:
        who = " ".join(x for x in (f"@{h(card.username)}" if card.username else "", h(card.name)) if x) or "—"
        lines.append(f"Кто: {who}")
        lines.append(f"В боте с: {fmt_date_msk(card.created_at)}"
                     f"{'' if card.is_active else ' · бот заблокирован пользователем'}"
                     f"{' · отписан от рассылок' if card.opt_out else ''}")
        lines.append(field("ID в панели", card.panel_id or "—"))
    else:
        lines.append("В базе бота нет.")
    if state is not None:
        if state.stale:
            sub = "панель недоступна"
        elif state.is_lifetime:
            sub = f"✅ бессрочно ({h(state.plan_code)})"
        elif state.active:
            sub = f"✅ {h(state.plan_code)} до {fmt_date_msk(state.expires_at, with_time=True)}"
        else:
            sub = f"❌ нет (было до {fmt_date_msk(state.expires_at)})" if state.expires_at else "❌ нет"
        lines.append(f"Подписка: {sub}")
    if card.trial_at:
        lines.append(f"Триал: {fmt_date_msk(card.trial_at)}")
    if card.promos:
        lines.append("Промо: " + ", ".join(h(p) for p in card.promos))
    lines.append(field("Оплачено всего", fmt_rub(card.paid_sum)))
    pay_lines = []
    for p in card.payments:
        plan = f" {h(p.plan_code)}/{p.months}м" if p.plan_code else ""
        pay_lines.append(f"• #{p.id} {h(p.provider)} {h(p.status)} {fmt_rub(p.amount)}{plan} {fmt_date_msk(p.paid_at)}")
    lines.append(f"Блок в боте: {'🚫 да' if bot_blocked else 'нет'} · Админ: {'👑 да' if is_admin else 'нет'}")
    return ui.admin_screen(
        f"Whois {card.telegram_id}", emoji=E.PROFILE,
        sections=[block(*lines), block(*pay_lines, title="Платежи", emoji="💳") if pay_lines else None],
    )


def referral_screen(st: Any) -> ui.Screen:
    if st is None:
        return ui.admin_screen("Рефералка", emoji="📊", lines=["Активаций промокода пока нет."])
    lines = [
        field("Активаций", st.activations), field("С зачетом", st.paying),
        field("Заработано Pro-месяцев", st.earned_months),
        field("Бонусов (целых)", f"{st.full_bonus} ({st.bonus:.2f})"),
        field("Уже выплачено", st.paid_out), field("Доступно к выдаче", st.available),
    ]
    if st.owner_id:
        lines.append(f"Владелец <code>{st.owner_id}</code> исключен из пула")
    top_lines = []
    for r in st.top or ():
        tag = f"{r.payments_after} плт" + ("+1 пред-кредит" if r.pre_credit else "")
        top_lines.append(f"• <code>{r.telegram_id}</code>: {r.months} мес ({tag})")
    return ui.admin_screen(
        f"Рефералка /{h(st.code)}", emoji="📊",
        hint="Выдал бонус? Зафиксируй: /referral_payout sun718 N комментарий",
        sections=[block(*lines), block(*top_lines, title="Топ приглашенных") if top_lines else None],
    )


def obhod_overview_screen(ov: dict) -> ui.Screen:
    return ui.admin_screen(
        "Обход", emoji="🛡",
        lines=[field("Активных", f"{ov.get('active', 0)} из {ov.get('total', 0)}")],
        hint="Карточка пользователя: /obhod <telegram_id>",
    )


def obhod_card_screen(info: Any) -> ui.Screen:
    used = fmt_gb(info.used_bytes) if info.used_bytes is not None else "—"
    cap = fmt_gb(info.limit_bytes) if info.limit_bytes is not None else "—"
    lines = [
        f"Статус: {'✅ активен' if info.active else '⛔ выключен'}",
        f"Трафик: {used} из {cap}", field("Срок", fmt_date_msk(info.expire_at)),
    ]
    if info.package:
        lines.append(f"Пакет: {h(info.package)} до {h(info.package_until or '—')}")
    return ui.admin_screen(f"Обход {info.telegram_id}", emoji="🛡", lines=lines)


def blocklist_screen(users: list, cards: list) -> ui.Screen:
    user_lines = [f"• <code>{h(e.key)}</code> {h(e.reason or '')}" for e in users[:30]] or ["—"]
    card_lines = [f"• <code>{h(e.key)}</code> {h(e.reason or '')}" for e in cards[:30]] or ["—"]
    return ui.admin_screen(
        "Стоп-лист", emoji="⛔", hint="Кому не продаем",
        sections=[block(*user_lines, title=f"Пользователи ({len(users)})"),
                  block(*card_lines, title=f"Карты ({len(cards)})"),
                  block(USAGE_STOPLIST, quote=False)],
    )


def promo_list_screen(rows: list) -> ui.Screen:
    lines = []
    for r in rows:
        mark = "🟢" if r.is_active else "⚪️"
        uses = f"{r.uses}/{r.max_uses}" if r.max_uses else f"{r.uses}"
        lines.append(f"{mark} <code>{h(r.code)}</code> +{r.days} дн. {h(r.plan_code or '')} · {h(r.audience)} · {uses}")
    return ui.admin_screen(
        "Промокоды", emoji="🎟",
        sections=[block(*lines) if lines else block("Пока нет ни одного."), block(USAGE_PROMO_NEW, quote=False)],
    )


def promo_card_screen(r: Any, bot_username: Optional[str] = None) -> ui.Screen:
    lines = [
        f"Тип: {h(r.kind)}, дней: {r.days}, тариф: {h(r.plan_code or 'текущий')}",
        f"Аудитория: {h(r.audience)}",
        f"Активаций: {r.uses}{' из ' + str(r.max_uses) if r.max_uses else ''}, на человека: {r.per_user_limit}",
        field("Действует до", fmt_date_msk(r.valid_until) if r.valid_until else "без срока"),
    ]
    if r.traffic_gb or r.devices:
        lines.append(f"Трафик: {r.traffic_gb or 0} ГБ, устройств: {r.devices or 0}")
    if bot_username:
        lines.append(f"Ссылка: <code>https://t.me/{h(bot_username)}?start={h(r.code)}</code>")
    title = f"{r.code} {'🟢 включен' if r.is_active else '⚪️ выключен'}"
    return ui.admin_screen(title, emoji="🎟", lines=lines)


def confirm_screen(question: str, *lines: str) -> ui.Screen:
    """Admin yes/no confirmation, laid out like the plain ``confirm`` type."""
    return ui.confirm(question, *lines)


def result_screen(kind: str, title: str, *lines: str, hint: str = "") -> ui.Screen:
    """A short admin-side result line (block command, sync, grant...)."""
    return ui.admin_screen(title, emoji=ui.KIND_EMOJI[kind], lines=list(lines), hint=hint)


def broadcast_step_screen(title: str, *lines: str, hint: str = "") -> ui.Screen:
    return ui.admin_screen(title, emoji=E.INPUT, lines=list(lines), hint=hint)


def bc_note(text: str, *, kind: str = "info") -> ui.Screen:
    """A short broadcast wizard/command notice or result, one line."""
    return result_screen(kind, "Рассылка", text)


BC_STEP_TEXT_SCREEN = broadcast_step_screen(
    "Новая рассылка, шаг 1/6: текст",
    "Пришли текст (HTML: &lt;b&gt;, &lt;i&gt;, &lt;a&gt;, &lt;code&gt;, &lt;blockquote&gt;).",
)
BC_STEP_PHOTO_SCREEN = broadcast_step_screen("Шаг 2/6: фото", "Пришли фото или нажми «Без фото».")
BC_STEP_BUTTONS_SCREEN = broadcast_step_screen(
    "Шаг 3/6: кнопки",
    "JSON-массив или «Без кнопок». Пример:",
    "<code>[{\"text\": \"Открыть сайт\", \"url\": \"https://example.com\"}]</code>",
    "Поля: text + url или text + callback_data. «Отписаться» и «Закрыть» добавятся сами.",
)
BC_STEP_SEGMENT_SCREEN = broadcast_step_screen("Шаг 4/6: кому")
BC_STEP_SUBKIND_SCREEN = broadcast_step_screen("Какая подписка?")
BC_STEP_DAYS_SCREEN = broadcast_step_screen(
    "Шаг 4/6: кому",
    "Пришли число дней N для «в пределах N дней» (истекает в ближайшие N дней для активных, истекла за "
    "последние N дней для истекших, триал за последние N дней) или 0, чтобы без ограничения.",
)
BC_STEP_IDS_SCREEN = broadcast_step_screen(
    "Шаг 4/6: кому", "Пришли Telegram ID через пробел, запятую или с новой строки (до 1000).",
)
BC_STEP_CREDIT_SCREEN = broadcast_step_screen(
    "Шаг 5/6: подарок", "Сколько дней начислить каждому, кому сообщение дошло?",
)
BC_STEP_SOUND_SCREEN = broadcast_step_screen("Шаг 6/6: звук")


def broadcast_draft_screen(info: Any, audience: int, state_title: str, segment_label: str,
                           credit_line: str) -> ui.Screen:
    lines = [
        field("Кому", f"{segment_label} (сейчас около {audience})"),
        f"Фото: {'да' if info.photo_file_id else 'нет'}, кнопок: {len(info.buttons)}",
        f"Звук: {'выкл' if info.disable_notification else 'вкл'}",
        field("Подарок", credit_line),
    ]
    return ui.admin_screen(f"Рассылка #{info.id} · {state_title}", emoji="📢", lines=lines)


def broadcast_progress_screen(info: Any, credited: Optional[int], state_title: str,
                              segment_label: str) -> ui.Screen:
    processed = info.delivered + info.failed + info.blocked
    pct = (processed * 100 // info.total) if info.total else 0
    lines = [
        field("Кому", segment_label),
        f"Всего: {info.total}, доставлено: {info.delivered}, ошибок: {info.failed}, заблокировали: {info.blocked}",
        f"Прогресс: {processed}/{info.total} ({pct}%)",
    ]
    if info.credit_days:
        lines.append(f"Подарок начислено: {credited if credited is not None else '—'}")
    if info.started_at:
        lines.append(field("Старт", fmt_date_msk(info.started_at, with_time=True)))
    if info.finished_at:
        lines.append(field("Финиш", fmt_date_msk(info.finished_at, with_time=True)))
    return ui.admin_screen(f"Рассылка #{info.id} · {state_title}", emoji="📢", lines=lines)


def broadcast_listing_screen(rows: Sequence[str]) -> ui.Screen:
    return ui.admin_screen("Рассылки", emoji="📢", hint="Последние 20", lines=list(rows) or ["Пока нет ни одной."])
