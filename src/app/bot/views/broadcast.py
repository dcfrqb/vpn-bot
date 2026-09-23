"""Broadcast admin screens (stream E): pure functions."""
from __future__ import annotations

from typing import Any, Optional

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import Adm, BcAdm
from app.bot.views import kb
from app.domain.texts import admin as T
from app.domain.texts import days_ru, fmt_date_msk, h

View = tuple[str, Optional[InlineKeyboardMarkup]]
STATE_TITLES = {"draft": "📝 черновик", "running": "🟡 идет", "done": "✅ завершена"}
CREDIT_CHOICES = (0, 3, 5, 7)


def segment_label(seg: Any) -> str:
    base = T.SEGMENT_TITLES.get(seg.kind, seg.kind)
    if seg.kind == "ids":
        return f"{base} ({len(seg.ids)})"
    parts = [base]
    if seg.days:
        parts.append(f"в пределах {days_ru(seg.days)}")
    if seg.sub_kind != "main":
        parts.append(f"подписка {h(seg.sub_kind)}")
    return ", ".join(parts)


def skip_kb(what: str) -> InlineKeyboardMarkup:
    title = "Без фото" if what == "photo" else "Без кнопок"
    return kb([[(title, BcAdm(a="skip", arg=what))], [("Отмена", BcAdm(a="abort"))]])


def segment_kb() -> InlineKeyboardMarkup:
    rows = [[(title, BcAdm(a="seg", arg=kind))] for kind, title in T.SEGMENT_TITLES.items()]
    rows.append([("Отмена", BcAdm(a="abort"))])
    return kb(rows)


def subkind_kb() -> InlineKeyboardMarkup:
    return kb([[("Основная подписка", BcAdm(a="sk", arg="main")), ("Обход", BcAdm(a="sk", arg="obhod"))]])


def credit_kb() -> InlineKeyboardMarkup:
    return kb([[(("Без подарка" if d == 0 else f"+{d} дн."), BcAdm(a="credit", arg=str(d))) for d in CREDIT_CHOICES]])


def sound_kb() -> InlineKeyboardMarkup:
    return kb([[("🔔 Со звуком", BcAdm(a="sound", arg="1")), ("🔕 Тихо", BcAdm(a="sound", arg="0"))]])


def draft(info: Any, audience: int) -> View:
    text = (
        f"📢 <b>Рассылка #{info.id}</b> · {STATE_TITLES.get(info.state, info.state)}\n\n"
        f"Кому: {segment_label(info.segment)} (сейчас около <b>{audience}</b>)\n"
        f"Фото: {'да' if info.photo_file_id else 'нет'}, кнопок: {len(info.buttons)}\n"
        f"Звук: {'выкл' if info.disable_notification else 'вкл'}\n"
        f"Подарок: {('+' + days_ru(info.credit_days)) if info.credit_days else 'нет'}"
    )
    rows = [[("👁 Превью себе", BcAdm(a="prev", id=info.id))]]
    if info.state == "draft":
        rows.append([("🚀 Запустить", BcAdm(a="go", id=info.id)), ("🗑 Удалить", BcAdm(a="del", id=info.id))])
    else:
        rows.append([("📈 Прогресс", BcAdm(a="stats", id=info.id))])
    rows.append([(T.BTN_BACK, BcAdm(a="list"))])
    return text, kb(rows)


def confirm_start(info: Any, audience: int) -> View:
    gift = f"\nКаждому, кому дойдет: +{days_ru(info.credit_days)}." if info.credit_days else ""
    text = (f"⚠️ Запустить рассылку <b>#{info.id}</b>?\n\n"
            f"Кому: {segment_label(info.segment)}, около <b>{audience}</b> получателей.{gift}")
    return text, kb([[("✅ Да, запустить", BcAdm(a="go2", id=info.id)), ("Отмена", BcAdm(a="show", id=info.id))]])


def progress(info: Any, credited: Optional[int] = None) -> View:
    processed = info.delivered + info.failed + info.blocked
    pct = (processed * 100 // info.total) if info.total else 0
    lines = [f"<b>Рассылка #{info.id}</b> · {STATE_TITLES.get(info.state, info.state)}", "",
             f"Кому: {segment_label(info.segment)}",
             f"Всего: {info.total}, доставлено: {info.delivered}, ошибок: {info.failed}, заблокировали: {info.blocked}",
             f"Прогресс: {processed}/{info.total} ({pct}%)"]
    if info.credit_days:
        lines.append(f"Подарок +{days_ru(info.credit_days)}: начислено {credited if credited is not None else '—'}")
    if info.started_at:
        lines.append(f"Старт: {fmt_date_msk(info.started_at, with_time=True)}")
    if info.finished_at:
        lines.append(f"Финиш: {fmt_date_msk(info.finished_at, with_time=True)}")
    rows = [[(T.BTN_REFRESH, BcAdm(a="stats", id=info.id))]]
    if info.state == "running":
        rows.append([("🛑 Остановить", BcAdm(a="cancel", id=info.id))])
    rows.append([(T.BTN_BACK, BcAdm(a="list"))])
    return "\n".join(lines), kb(rows)


def listing(items: list) -> View:
    lines = ["📢 <b>Рассылки</b> (последние 20)", ""]
    if not items:
        lines.append("Пока нет ни одной.")
    for bc in items:
        lines.append(f"#{bc.id} {STATE_TITLES.get(bc.state, bc.state)} · {segment_label(bc.segment)} · "
                     f"{bc.delivered}/{bc.total}")
    rows = [[("➕ Новая рассылка", BcAdm(a="new"))]]
    rows += [[(f"#{bc.id} {STATE_TITLES.get(bc.state, '')}", BcAdm(a="show", id=bc.id))] for bc in items[:10]]
    rows.append([(T.BTN_PANEL, Adm(s="panel", a="open"))])
    return "\n".join(lines), kb(rows)
