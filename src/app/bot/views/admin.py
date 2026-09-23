"""Admin screens (stream E): pure functions (DTO -> text, keyboard)."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import Adm, BcAdm, PromoAdm
from app.bot.views import kb, url_btn
from app.domain.texts import admin as T
from app.domain.texts import fmt_date_msk, fmt_gb, fmt_rub, h

View = tuple[str, Optional[InlineKeyboardMarkup]]

BACK_TO_PANEL = [(T.BTN_PANEL, Adm(s="panel", a="open"))]


def home(stats: Any) -> View:
    text = (
        f"{T.PANEL_TITLE}\n\n"
        f"👥 Пользователей: <b>{stats.total_users}</b> (сегодня +{stats.today_users})\n"
        f"🔄 Активных подписок: <b>{stats.active_subscriptions}</b>\n"
        f"🎁 Триалов: {stats.trials_total} (сегодня {stats.trials_today})\n"
        f"💰 Выручка сегодня: {fmt_rub(stats.revenue_today)}, за 30 дней: {fmt_rub(stats.revenue_30d)}"
    )
    return text, kb([
        [(T.BTN_STATS, Adm(s="stats", a="open")), (T.BTN_USERS, Adm(s="users", a="open"))],
        [(T.BTN_PAYMENTS, Adm(s="payments", a="open")), (T.BTN_PROMO, PromoAdm(a="list"))],
        [(T.BTN_BROADCASTS, BcAdm(a="list")), (T.BTN_OBHOD, Adm(s="obhod", a="open"))],
        [(T.BTN_BLOCKLIST, Adm(s="block", a="open")), (T.BTN_REFERRAL, Adm(s="ref", a="open"))],
        [(T.BTN_MAINT, Adm(s="maint", a="show"))],  # stream C: bot/routers/admin/panel.py
    ])


def stats(s: Any) -> View:
    text = (
        "📊 <b>Статистика</b> (основные подписки, без тестовых и промо-платежей)\n\n"
        f"👥 Пользователи: <b>{s.total_users}</b>, сегодня +{s.today_users}\n"
        f"🔄 Активные подписки: <b>{s.active_subscriptions}</b>\n"
        f"🎁 Триалы: {s.trials_total}, сегодня {s.trials_today}\n\n"
        f"💳 Оплат: <b>{s.paid_total}</b>, сегодня {s.paid_today}\n"
        f"💰 Выручка: <b>{fmt_rub(s.revenue_total)}</b>\n"
        f"• сегодня: {fmt_rub(s.revenue_today)}\n"
        f"• за 30 дней: {fmt_rub(s.revenue_30d)}\n"
        f"↩️ Возвращено: {fmt_rub(s.refunded_total)}"
    )
    return text, kb([[(T.BTN_REFRESH, Adm(s="stats", a="open"))], BACK_TO_PANEL])


def _pager(section: str, page: int, total_pages: int, suffix: str = "") -> list:
    row = []
    if page > 1:
        row.append((T.BTN_PREV, Adm(s=section, a="page", arg=f"{page - 1}{suffix}")))
    if page < total_pages:
        row.append((T.BTN_NEXT, Adm(s=section, a="page", arg=f"{page + 1}{suffix}")))
    return row


def users(data: dict) -> View:
    lines = [f"👥 <b>Пользователи</b> (всего {data.get('total', 0)}, стр. {data.get('page', 1)}"
             f" из {max(1, int(data.get('total_pages') or 1))})", ""]
    for u in data.get("users") or []:
        name = u.get("username") or u.get("first_name") or "—"
        plan = u.get("subscription_plan") or "—"
        lines.append(f"• <code>{h(u.get('telegram_id'))}</code> {h(name)} · {h(plan)}")
    if not data.get("users"):
        lines.append("Пусто.")
    rows = [_pager("users", int(data.get("page", 1)), int(data.get("total_pages", 1))), BACK_TO_PANEL]
    return "\n".join(lines), kb(rows)


PAYMENT_FILTERS = {"all": "Все", "succeeded": "Успешные", "pending": "Ожидают", "canceled": "Отмененные",
                   "failed": "Неудачные"}
_STATUS_ICON = {"succeeded": "✅", "pending": "⏳", "canceled": "❌", "failed": "⚠️"}


def payments(data: dict, flt: str) -> View:
    flt = flt if flt in PAYMENT_FILTERS else "all"
    lines = [f"💳 <b>Платежи</b> · {PAYMENT_FILTERS[flt]}",
             f"Всего: {data.get('total', 0)}, стр. {data.get('page', 1)} из {max(1, int(data.get('total_pages') or 1))}",
             ""]
    for i, p in enumerate(data.get("payments") or [], 1):
        icon = _STATUS_ICON.get(p.get("status"), "❓")
        who = f"@{h(p.get('username'))}" if p.get("username") else f"<code>{h(p.get('telegram_id') or '—')}</code>"
        lines.append(f"{i}. {icon} {fmt_rub(p.get('amount'))} · {who} · {h(p.get('provider'))}")
    if not data.get("payments"):
        lines.append("Платежей не найдено.")
    rows = [
        _pager("payments", int(data.get("page", 1)), int(data.get("total_pages", 1)), f".{flt}"),
        [("📊 Все", Adm(s="payments", a="filter", arg="all")), ("✅", Adm(s="payments", a="filter", arg="succeeded")),
         ("⏳", Adm(s="payments", a="filter", arg="pending"))],
        BACK_TO_PANEL,
    ]
    return "\n".join(lines), kb(rows)


def whois(card: Any, state: Any, *, bot_blocked: bool, is_admin: bool) -> str:
    lines = [f"<b>Whois {card.telegram_id}</b>"]
    if card.known:
        who = " ".join(x for x in (f"@{h(card.username)}" if card.username else "", h(card.name)) if x) or "—"
        lines.append(f"Кто: {who}")
        lines.append(f"В боте с: {fmt_date_msk(card.created_at)}"
                     f"{'' if card.is_active else ' · бот заблокирован пользователем'}"
                     f"{' · отписан от рассылок' if card.opt_out else ''}")
        lines.append(f"ID в панели: <code>{h(card.panel_id or '—')}</code>")
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
    lines.append(f"Оплачено всего: {fmt_rub(card.paid_sum)}")
    for p in card.payments:
        plan = f" {h(p.plan_code)}/{p.months}м" if p.plan_code else ""
        lines.append(f"• #{p.id} {h(p.provider)} {h(p.status)} {fmt_rub(p.amount)}{plan} {fmt_date_msk(p.paid_at)}")
    lines.append(f"Блок в боте: {'🚫 да' if bot_blocked else 'нет'} · Админ: {'👑 да' if is_admin else 'нет'}")
    return "\n".join(lines)


def request_keyboard(section: str, arg: str, user_id: int) -> InlineKeyboardMarkup:
    """/friend (section friend) and /admin (section promo_req) request buttons."""
    return kb([
        [(T.BTN_GRANT_1M, Adm(s=section, a="grant_1m", arg=arg))],
        [(T.BTN_GRANT_3M, Adm(s=section, a="grant_3m", arg=arg))],
        [(T.BTN_GRANT_FOREVER, Adm(s=section, a="grant_forever", arg=arg))],
        [(T.BTN_REJECT, Adm(s=section, a="reject", arg=arg))],
        [url_btn("📩 Написать пользователю", f"tg://user?id={int(user_id)}")],
    ])


def referral(st: Any) -> str:
    if st is None:
        return "Активаций промокода пока нет."
    lines = [f"📊 <b>Рефералка /{h(st.code)}</b>", "",
             f"Активаций: <b>{st.activations}</b>, с зачетом: <b>{st.paying}</b>", "",
             f"💰 Заработано Pro-месяцев: {st.earned_months}",
             f"🎁 Бонусов (целых): {st.full_bonus} ({st.bonus:.2f})",
             f"💸 Уже выплачено: {st.paid_out}",
             f"✅ <b>Доступно к выдаче: {st.available}</b>"]
    if st.owner_id:
        lines.append(f"\n<i>Владелец <code>{st.owner_id}</code> исключен из пула</i>")
    if st.top:
        lines.append("\n<b>Топ приглашенных:</b>")
        for r in st.top:
            tag = f"{r.payments_after} плт" + ("+1 пред-кредит" if r.pre_credit else "")
            lines.append(f"• <code>{r.telegram_id}</code>: {r.months} мес ({tag})")
    lines.append("\n<i>Выдал бонус? Зафиксируй:</i> <code>/referral_payout sun718 N комментарий</code>")
    return "\n".join(lines)


def obhod_overview(ov: dict) -> View:
    text = (f"🛡 <b>Обход</b>\n\nАктивных: <b>{ov.get('active', 0)}</b> из {ov.get('total', 0)}\n\n"
            "Карточка пользователя: <code>/obhod &lt;telegram_id&gt;</code>")
    return text, kb([BACK_TO_PANEL])


def obhod_card(info: Any, packages: Sequence[tuple[str, str]]) -> View:
    tg = info.telegram_id
    if not info.exists:
        return f"🛡 У <code>{tg}</code> нет подписки обхода (она выдается с Pro).", kb([BACK_TO_PANEL])
    used = fmt_gb(info.used_bytes) if info.used_bytes is not None else "—"
    cap = fmt_gb(info.limit_bytes) if info.limit_bytes is not None else "—"
    lines = [f"🛡 <b>Обход {tg}</b>", f"Статус: {'✅ активен' if info.active else '⛔ выключен'}",
             f"Трафик: {used} из {cap}", f"Срок: {fmt_date_msk(info.expire_at)}"]
    if info.package:
        lines.append(f"Пакет: {h(info.package)} до {h(info.package_until or '—')}")
    rows = [[(f"➕ {title}", Adm(s="obhod", a="pkg", arg=f"{tg}.{code}"))] for code, title in packages]
    rows.append([("↩️ Базовый лимит", Adm(s="obhod", a="base", arg=str(tg))),
                 ("⛔ Выключить", Adm(s="obhod", a="off", arg=str(tg)))])
    rows.append([(T.BTN_REFRESH, Adm(s="obhod", a="show", arg=str(tg)))])
    rows.append(BACK_TO_PANEL)
    return "\n".join(lines), kb(rows)


def confirm(text: str, yes: Any, no: Any) -> View:
    return text, kb([[("✅ Да", yes), ("Отмена", no)]])


def blocklist(users: list, cards: list) -> View:
    lines = ["⛔ <b>Стоп-лист</b> (кому не продаем)", ""]
    lines.append(f"<b>Пользователи ({len(users)}):</b>")
    lines += [f"• <code>{h(e.key)}</code> {h(e.reason or '')}" for e in users[:30]] or ["—"]
    lines.append(f"\n<b>Карты ({len(cards)}):</b>")
    lines += [f"• <code>{h(e.key)}</code> {h(e.reason or '')}" for e in cards[:30]] or ["—"]
    lines.append("")
    lines.append(T.USAGE_STOPLIST)
    return "\n".join(lines), kb([BACK_TO_PANEL])


# ----------------------------------------------------------------- promo codes


def promo_list(rows: list) -> View:
    lines = ["🎟 <b>Промокоды</b>", ""]
    if not rows:
        lines.append("Пока нет ни одного.")
    for r in rows:
        mark = "🟢" if r.is_active else "⚪️"
        uses = f"{r.uses}/{r.max_uses}" if r.max_uses else f"{r.uses}"
        lines.append(f"{mark} <code>{h(r.code)}</code> +{r.days} дн. {h(r.plan_code or '')} · {h(r.audience)} · {uses}")
    lines += ["", T.USAGE_PROMO_NEW]
    buttons = [[(f"{'🟢' if r.is_active else '⚪️'} {r.code}", PromoAdm(a="show", id=r.id))] for r in rows[:15]]
    buttons.append(BACK_TO_PANEL)
    return "\n".join(lines), kb(buttons)


def promo_card(r: Any, bot_username: Optional[str] = None) -> View:
    lines = [f"🎟 <b>{h(r.code)}</b> {'🟢 включен' if r.is_active else '⚪️ выключен'}",
             f"Тип: {h(r.kind)}, дней: {r.days}, тариф: {h(r.plan_code or 'текущий')}",
             f"Аудитория: {h(r.audience)}",
             f"Активаций: {r.uses}{' из ' + str(r.max_uses) if r.max_uses else ''}, на человека: {r.per_user_limit}",
             f"Действует до: {fmt_date_msk(r.valid_until) if r.valid_until else 'без срока'}"]
    if r.traffic_gb or r.devices:
        lines.append(f"Трафик: {r.traffic_gb or 0} ГБ, устройств: {r.devices or 0}")
    if bot_username:
        lines.append(f"Ссылка: <code>https://t.me/{h(bot_username)}?start={h(r.code)}</code>")
    toggle = (("⚪️ Выключить", PromoAdm(a="off", id=r.id)) if r.is_active
              else ("🟢 Включить", PromoAdm(a="on", id=r.id)))
    return "\n".join(lines), kb([[toggle], [(T.BTN_BACK, PromoAdm(a="list"))]])
