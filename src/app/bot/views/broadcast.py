"""Broadcast admin screens (stream E): pure functions, screen kit (release 3.0)."""
from __future__ import annotations

from typing import Any, Optional

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import BcAdm
from app.bot.views import kit
from app.domain.texts import admin as T
from app.domain.texts import days_ru, h
from app.domain.texts.ui import B

View = kit.View
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
    return kit.keyboard(primary=[kit.action(title, BcAdm(a="skip", arg=what))],
                        secondary=[kit.action(B.CANCEL, BcAdm(a="abort"))])


def segment_kb() -> InlineKeyboardMarkup:
    options = [kit.action(title, BcAdm(a="seg", arg=kind)) for kind, title in T.SEGMENT_TITLES.items()]
    return kit.keyboard(options=options, secondary=[kit.action(B.CANCEL, BcAdm(a="abort"))])


def subkind_kb() -> InlineKeyboardMarkup:
    return kit.keyboard(options=[kit.pair(kit.action("Основная подписка", BcAdm(a="sk", arg="main")),
                                          kit.action("Обход", BcAdm(a="sk", arg="obhod")))])


def credit_kb() -> InlineKeyboardMarkup:
    row = [kit.action(("Без подарка" if d == 0 else f"+{d} дн."), BcAdm(a="credit", arg=str(d))) for d in CREDIT_CHOICES]
    return kit.keyboard(options=[row])


def sound_kb() -> InlineKeyboardMarkup:
    return kit.keyboard(options=[kit.pair(kit.action("🔔 Со звуком", BcAdm(a="sound", arg="1")),
                                          kit.action("🔕 Тихо", BcAdm(a="sound", arg="0")))])


def draft(info: Any, audience: int) -> View:
    credit_line = ("+" + days_ru(info.credit_days)) if info.credit_days else "нет"
    screen = T.broadcast_draft_screen(info, audience, STATE_TITLES.get(info.state, info.state),
                                      segment_label(info.segment), credit_line)
    primary = [kit.action("👁 Превью себе", BcAdm(a="prev", id=info.id))]
    if info.state == "draft":
        secondary = [kit.pair(kit.action("🚀 Запустить", BcAdm(a="go", id=info.id)),
                              kit.action("🗑 Удалить", BcAdm(a="del", id=info.id)))]
    else:
        secondary = [kit.action("📈 Прогресс", BcAdm(a="stats", id=info.id))]
    return kit.view(screen, primary=primary, secondary=secondary,
                    footer=kit.Footer.back_only(BcAdm(a="list"), label=B.BACK))


def confirm_start(info: Any, audience: int) -> View:
    gift = f" Каждому, кому дойдет: +{days_ru(info.credit_days)}." if info.credit_days else ""
    lines = [f"Кому: {segment_label(info.segment)}, около {audience} получателей.{gift}"]
    screen = T.confirm_screen(f"Запустить рассылку #{info.id}?", *lines)
    return kit.view(screen, primary=[kit.pair(kit.action("✅ Да, запустить", BcAdm(a="go2", id=info.id)),
                                              kit.action(B.CANCEL, BcAdm(a="show", id=info.id)))])


def progress(info: Any, credited: Optional[int] = None) -> View:
    screen = T.broadcast_progress_screen(info, credited, STATE_TITLES.get(info.state, info.state),
                                         segment_label(info.segment))
    primary = [kit.action(T.BTN_REFRESH, BcAdm(a="stats", id=info.id))]
    secondary = [kit.action("🛑 Остановить", BcAdm(a="cancel", id=info.id))] if info.state == "running" else []
    return kit.view(screen, primary=primary, secondary=secondary,
                    footer=kit.Footer.back_only(BcAdm(a="list"), label=B.BACK))


def listing(items: list) -> View:
    rows = [f"#{bc.id} {STATE_TITLES.get(bc.state, bc.state)} · {segment_label(bc.segment)} · "
           f"{bc.delivered}/{bc.total}" for bc in items]
    screen = T.broadcast_listing_screen(rows)
    options = [kit.action(f"#{bc.id} {STATE_TITLES.get(bc.state, '')}", BcAdm(a="show", id=bc.id)) for bc in items[:10]]
    return kit.view(screen, primary=[kit.action("➕ Новая рассылка", BcAdm(a="new"))], options=options,
                    footer=kit.Footer.to_admin())


__all__ = [
    "CREDIT_CHOICES", "STATE_TITLES", "View", "confirm_start", "credit_kb", "draft", "listing", "progress",
    "segment_kb", "segment_label", "skip_kb", "sound_kb", "subkind_kb",
]
