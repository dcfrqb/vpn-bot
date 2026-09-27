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
BTN_TO_LIST = "⬅️ К рассылкам"


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


# Wizard steps and where «⬅️ Назад» leads from each (the router passes the real
# previous step when it depends on the chosen segment: days/ids/credit).
STEP_TEXT, STEP_PHOTO, STEP_BUTTONS, STEP_SEGMENT, STEP_SUBKIND = "text", "photo", "buttons", "segment", "subkind"
STEP_DAYS, STEP_IDS, STEP_CREDIT, STEP_SOUND = "days", "ids", "credit", "sound"
STEPS = (STEP_TEXT, STEP_PHOTO, STEP_BUTTONS, STEP_SEGMENT, STEP_SUBKIND, STEP_DAYS, STEP_IDS, STEP_CREDIT, STEP_SOUND)
PREV_STEP = {STEP_PHOTO: STEP_TEXT, STEP_BUTTONS: STEP_PHOTO, STEP_SEGMENT: STEP_BUTTONS,
             STEP_SUBKIND: STEP_SEGMENT, STEP_DAYS: STEP_SEGMENT, STEP_IDS: STEP_SEGMENT,
             STEP_CREDIT: STEP_SEGMENT, STEP_SOUND: STEP_CREDIT}


def wizard_footer(back: Optional[str] = None) -> kit.Footer:
    """Every wizard step: [⬅️ Назад] (previous step) / [✖️ Отменить рассылку] [👑 В админку]."""
    return kit.Footer.wizard(BcAdm(a="abort"), B.BC_ABORT, BcAdm(a="back", arg=back) if back else None)


def wizard_kb(back: Optional[str] = None, **parts: Any) -> InlineKeyboardMarkup:
    return kit.keyboard(footer=wizard_footer(back), **parts)


def text_kb() -> InlineKeyboardMarkup:
    """Step 1 has nothing before it: cancel and admin only."""
    return wizard_kb(None)


def skip_kb(what: str) -> InlineKeyboardMarkup:
    title = "Без фото" if what == "photo" else "Без кнопок"
    step = STEP_PHOTO if what == "photo" else STEP_BUTTONS
    return wizard_kb(PREV_STEP[step], primary=[kit.action(title, BcAdm(a="skip", arg=what))])


def segment_kb() -> InlineKeyboardMarkup:
    options = [kit.action(title, BcAdm(a="seg", arg=kind)) for kind, title in T.SEGMENT_TITLES.items()]
    return wizard_kb(PREV_STEP[STEP_SEGMENT], options=options)


def subkind_kb() -> InlineKeyboardMarkup:
    return wizard_kb(PREV_STEP[STEP_SUBKIND],
                     options=[kit.pair(kit.action("Основная подписка", BcAdm(a="sk", arg="main")),
                                       kit.action("Обход", BcAdm(a="sk", arg="obhod")))])


def days_kb(back: str = STEP_SEGMENT) -> InlineKeyboardMarkup:
    return wizard_kb(back)


def ids_kb() -> InlineKeyboardMarkup:
    return wizard_kb(PREV_STEP[STEP_IDS])


def credit_kb(back: str = STEP_SEGMENT) -> InlineKeyboardMarkup:
    row = [kit.action(("Без подарка" if d == 0 else f"+{d} дн."), BcAdm(a="credit", arg=str(d))) for d in CREDIT_CHOICES]
    return wizard_kb(back, options=[row])


def sound_kb() -> InlineKeyboardMarkup:
    return wizard_kb(PREV_STEP[STEP_SOUND],
                     options=[kit.pair(kit.action("🔔 Со звуком", BcAdm(a="sound", arg="1")),
                                       kit.action("🔕 Тихо", BcAdm(a="sound", arg="0")))])


def exit_kb() -> InlineKeyboardMarkup:
    """After the wizard or a broadcast command: [⬅️ К рассылкам] [👑 В админку]."""
    return kit.keyboard(footer=kit.Footer.back_admin(BcAdm(a="list"), label=BTN_TO_LIST))


def started_kb(bid: int) -> InlineKeyboardMarkup:
    """After «✅ Да, запустить»: progress, then the way back."""
    return kit.keyboard(primary=[kit.action("📈 Прогресс", BcAdm(a="stats", id=bid))],
                        footer=kit.Footer.back_admin(BcAdm(a="list"), label=BTN_TO_LIST))


def note(screen: Any) -> View:
    """A broadcast notice/result outside the wizard, with the way back."""
    return kit.view(screen, footer=kit.Footer.back_admin(BcAdm(a="list"), label=BTN_TO_LIST))


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
                    footer=kit.Footer.back_admin(BcAdm(a="list"), label=BTN_TO_LIST))


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
                    footer=kit.Footer.back_admin(BcAdm(a="list"), label=BTN_TO_LIST))


def listing(items: list) -> View:
    rows = [f"#{bc.id} {STATE_TITLES.get(bc.state, bc.state)} · {segment_label(bc.segment)} · "
           f"{bc.delivered}/{bc.total}" for bc in items]
    screen = T.broadcast_listing_screen(rows)
    options = [kit.action(f"#{bc.id} {STATE_TITLES.get(bc.state, '')}", BcAdm(a="show", id=bc.id)) for bc in items[:10]]
    return kit.view(screen, primary=[kit.action("➕ Новая рассылка", BcAdm(a="new"))], options=options,
                    footer=kit.Footer.to_admin())


__all__ = [
    "BTN_TO_LIST", "CREDIT_CHOICES", "PREV_STEP", "STATE_TITLES", "STEPS", "View", "confirm_start", "credit_kb",
    "days_kb", "draft", "exit_kb", "ids_kb", "listing", "note", "progress", "segment_kb", "segment_label", "skip_kb",
    "sound_kb", "started_kb", "subkind_kb", "text_kb", "wizard_footer", "wizard_kb",
]
