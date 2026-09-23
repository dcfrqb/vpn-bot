"""Admin: segmented broadcasts with credit_days (BcAdm); user buttons Bc(unsub|close).

Owner stream: E (Growth & admin).

Two sub-routers:
- ``user_router`` (everybody): /stop, «Отписаться» (bc:unsub), «Закрыть» (bc:close);
- ``admin_router`` (AdminGuard): wizard (text, photo, buttons, segment with
  sub_kind / within N days / explicit ids, gift days, sound) -> draft card
  (preview to self, start with confirmation, delete), progress, stop, and
  the 2.x commands /bc_new, /bc_list, /bc_preview, /bc_send, /bc_send_to,
  /bc_stats, /bc_cancel.
"""
from __future__ import annotations

import json
import re
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.broadcast_sender import AiogramBroadcastSender
from app.bot.callbacks import Bc, BcAdm
from app.bot.middlewares.admin_guard import guard_router
from app.bot.views import broadcast as V
from app.bot.views import render
from app.domain.texts import admin as T
from app.domain.texts import h
from app.logger import logger
from app.services import broadcast as svc

router = Router(name="r3_admin_broadcast")
user_router = Router(name="r3_broadcast_user")
admin_router = guard_router(Router(name="r3_broadcast_admin"))
router.include_router(user_router)
router.include_router(admin_router)


# =============================================================================
# Users: /stop, Bc(unsub), Bc(close)
# =============================================================================


@user_router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    await svc.set_opt_out(message.from_user.id, True)
    await message.answer(T.STOP_DONE)


@user_router.callback_query(Bc.filter(F.a == "unsub"))
async def cb_unsub(callback: CallbackQuery) -> None:
    await svc.set_opt_out(callback.from_user.id, True)
    await callback.answer(T.UNSUB_ALERT)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 - message too old or already without buttons
        pass


@user_router.callback_query(Bc.filter(F.a == "close"))
async def cb_close(callback: CallbackQuery) -> None:
    try:
        await callback.message.delete()
    except Exception:  # noqa: BLE001 - older than 48 h: at least drop the buttons
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
    try:
        await callback.answer()
    except Exception:  # noqa: BLE001
        pass


# =============================================================================
# Admin wizard
# =============================================================================


class BcWizard(StatesGroup):
    text = State()
    photo = State()
    buttons = State()
    segment = State()
    days = State()
    ids = State()
    credit = State()
    sound = State()


WIZARD = StateFilter(BcWizard)


@admin_router.message(Command("bc_new"))
async def cmd_new(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BcWizard.text)
    await render(message, T.BC_STEP_TEXT)


@admin_router.callback_query(BcAdm.filter(F.a == "new"))
async def cb_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BcWizard.text)
    await render(callback, T.BC_STEP_TEXT)


@admin_router.message(WIZARD, Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await render(message, T.BC_CANCELLED)


@admin_router.callback_query(BcAdm.filter(F.a == "abort"))
async def cb_abort(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await render(callback, T.BC_CANCELLED)


@admin_router.message(StateFilter(BcWizard.text), F.text)
async def step_text(message: Message, state: FSMContext) -> None:
    text = message.html_text or message.text or ""
    if not text.strip():
        await render(message, T.BC_EMPTY_TEXT)
        return
    if len(text) > 4000:
        await render(message, T.BC_TOO_LONG.format(n=len(text)))
        return
    await state.update_data(text_html=text)
    await state.set_state(BcWizard.photo)
    await render(message, T.BC_STEP_PHOTO, V.skip_kb("photo"))


@admin_router.message(StateFilter(BcWizard.photo), F.photo)
async def step_photo(message: Message, state: FSMContext) -> None:
    await state.update_data(photo=message.photo[-1].file_id)
    await state.set_state(BcWizard.buttons)
    await render(message, T.BC_STEP_BUTTONS, V.skip_kb("buttons"))


def parse_buttons(raw: str) -> list[dict]:
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("нужен массив")
    for item in parsed:
        if not isinstance(item, dict) or not item.get("text"):
            raise ValueError("у каждой кнопки нужен text")
        if not (item.get("url") or item.get("callback_data")):
            raise ValueError("у каждой кнопки нужен url или callback_data")
        if item.get("callback_data") and len(str(item["callback_data"]).encode()) > 64:
            raise ValueError("callback_data длиннее 64 байт")
    return parsed


@admin_router.message(StateFilter(BcWizard.buttons), F.text)
async def step_buttons(message: Message, state: FSMContext) -> None:
    try:
        buttons = parse_buttons(message.text)
    except (ValueError, TypeError) as err:
        await render(message, T.BC_BAD_BUTTONS.format(err=h(str(err))), V.skip_kb("buttons"))
        return
    await state.update_data(buttons=buttons)
    await state.set_state(BcWizard.segment)
    await render(message, T.BC_STEP_SEGMENT, V.segment_kb())


@admin_router.callback_query(StateFilter(BcWizard.photo, BcWizard.buttons), BcAdm.filter(F.a == "skip"))
async def cb_skip(callback: CallbackQuery, callback_data: BcAdm, state: FSMContext) -> None:
    if callback_data.arg == "photo":
        await state.update_data(photo=None)
        await state.set_state(BcWizard.buttons)
        await render(callback, T.BC_STEP_BUTTONS, V.skip_kb("buttons"))
    else:
        await state.update_data(buttons=None)
        await state.set_state(BcWizard.segment)
        await render(callback, T.BC_STEP_SEGMENT, V.segment_kb())


@admin_router.callback_query(StateFilter(BcWizard.segment), BcAdm.filter(F.a == "seg"))
async def cb_segment(callback: CallbackQuery, callback_data: BcAdm, state: FSMContext) -> None:
    kind = callback_data.arg
    if kind not in svc.SEGMENTS:
        await callback.answer("Неизвестный сегмент", show_alert=True)
        return
    await state.update_data(kind=kind, sub_kind="main", days=None, ids=[])
    if kind in (svc.SEGMENT_ACTIVE, svc.SEGMENT_EXPIRED):
        await render(callback, "Какая подписка?", V.subkind_kb())
    elif kind == svc.SEGMENT_TRIAL_NC:
        await state.set_state(BcWizard.days)
        await render(callback, T.BC_STEP_DAYS)
    elif kind == svc.SEGMENT_IDS:
        await state.set_state(BcWizard.ids)
        await render(callback, T.BC_STEP_IDS)
    else:
        await _ask_credit(callback, state)


@admin_router.callback_query(StateFilter(BcWizard.segment), BcAdm.filter(F.a == "sk"))
async def cb_subkind(callback: CallbackQuery, callback_data: BcAdm, state: FSMContext) -> None:
    await state.update_data(sub_kind=callback_data.arg if callback_data.arg in svc.SUB_KINDS else "main")
    await state.set_state(BcWizard.days)
    await render(callback, T.BC_STEP_DAYS)


@admin_router.message(StateFilter(BcWizard.days), F.text)
async def step_days(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) > 3650:
        await render(message, T.BC_BAD_NUMBER.format(max=3650))
        return
    await state.update_data(days=int(raw) or None)
    await _ask_credit(message, state)


def parse_ids(raw: str) -> list[int]:
    ids = sorted({int(x) for x in re.findall(r"\d{3,20}", raw or "")})
    return ids[: svc.MAX_IDS]


@admin_router.message(StateFilter(BcWizard.ids), F.text)
async def step_ids(message: Message, state: FSMContext) -> None:
    ids = parse_ids(message.text)
    if not ids:
        await render(message, T.BC_BAD_IDS)
        return
    await state.update_data(ids=ids)
    await _ask_credit(message, state)


async def _ask_credit(event: Any, state: FSMContext) -> None:
    await state.set_state(BcWizard.credit)
    await render(event, T.BC_STEP_CREDIT, V.credit_kb())


@admin_router.callback_query(StateFilter(BcWizard.credit), BcAdm.filter(F.a == "credit"))
async def cb_credit(callback: CallbackQuery, callback_data: BcAdm, state: FSMContext) -> None:
    days = int(callback_data.arg) if callback_data.arg.isdigit() else 0
    await state.update_data(credit=min(days, 365))
    await state.set_state(BcWizard.sound)
    await render(callback, T.BC_STEP_SOUND, V.sound_kb())


@admin_router.callback_query(StateFilter(BcWizard.sound), BcAdm.filter(F.a == "sound"))
async def cb_sound(callback: CallbackQuery, callback_data: BcAdm, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        seg = svc.Segment(kind=data.get("kind") or "all", sub_kind=data.get("sub_kind") or "main",
                          days=data.get("days"), ids=tuple(data.get("ids") or ()))
        bid = await svc.create_draft(text_html=data["text_html"], photo_file_id=data.get("photo"),
                                     buttons=data.get("buttons"), segment=seg,
                                     disable_notification=callback_data.arg != "1",
                                     credit_days=int(data.get("credit") or 0), created_by=callback.from_user.id)
    except (KeyError, ValueError) as err:
        logger.warning(f"broadcast draft: bad wizard data ({type(err).__name__})")
        await render(callback, T.BC_CANCELLED)
        return
    await _show(callback, bid)


# =============================================================================
# Draft card, start, progress
# =============================================================================


async def _show(event: Any, bid: int) -> None:
    info = await svc.get_broadcast(bid)
    if info is None:
        await render(event, T.BC_NOT_FOUND)
        return
    if info.state == "draft":
        await render(event, *V.draft(info, await svc.count_segment(info.segment)))
    else:
        await _progress(event, info)


async def _progress(event: Any, info: Any) -> None:
    credited = None
    if info.credit_days:
        try:
            from app.services.grants import SqlRedemptionLedger

            credited = await SqlRedemptionLedger().count(svc.credit_code(info.id), "applied")
        except Exception:  # noqa: BLE001 - counters only
            credited = None
    await render(event, *V.progress(info, credited))


async def _list(event: Any) -> None:
    await render(event, *V.listing(await svc.list_broadcasts(20)))


@admin_router.message(Command("bc_list"))
async def cmd_list(message: Message) -> None:
    await _list(message)


@admin_router.callback_query(BcAdm.filter(F.a == "list"))
async def cb_list(callback: CallbackQuery) -> None:
    await _list(callback)


@admin_router.callback_query(BcAdm.filter(F.a == "show"))
async def cb_show(callback: CallbackQuery, callback_data: BcAdm) -> None:
    await _show(callback, callback_data.id)


async def _preview(event: Any, bid: int, chat_id: int) -> None:
    info = await svc.get_broadcast(bid)
    if info is None:
        await render(event, T.BC_NOT_FOUND)
        return
    res = await AiogramBroadcastSender(event.bot).preview(chat_id, info)
    if res.status != "sent":
        await render(event, T.BC_PREVIEW_FAILED.format(err=h(res.error or res.status)))
    elif isinstance(event, CallbackQuery):
        await event.answer("Превью отправлено")


@admin_router.callback_query(BcAdm.filter(F.a == "prev"))
async def cb_preview(callback: CallbackQuery, callback_data: BcAdm) -> None:
    await _preview(callback, callback_data.id, callback.from_user.id)


async def _confirm(event: Any, bid: int) -> None:
    info = await svc.get_broadcast(bid)
    if info is None:
        await render(event, T.BC_NOT_FOUND)
    elif info.state != "draft":
        await render(event, T.BC_ALREADY_DONE if info.state == "done" else T.BC_ALREADY_RUNNING)
    else:
        await render(event, *V.confirm_start(info, await svc.count_segment(info.segment)))


@admin_router.callback_query(BcAdm.filter(F.a == "go"))
async def cb_go(callback: CallbackQuery, callback_data: BcAdm) -> None:
    await _confirm(callback, callback_data.id)


@admin_router.callback_query(BcAdm.filter(F.a == "go2"))
async def cb_go_confirmed(callback: CallbackQuery, callback_data: BcAdm, container: Any) -> None:
    info = await svc.get_broadcast(callback_data.id)
    if info is None:
        await render(callback, T.BC_NOT_FOUND)
        return
    if info.state != "draft":
        await callback.answer(T.BC_ALREADY_RUNNING, show_alert=True)
        return
    started = await svc.start_broadcast(callback.bot, info.id, credit=svc.credits_from_container(container))
    await render(callback, T.BC_STARTED if started else T.BC_ALREADY_RUNNING)


@admin_router.callback_query(BcAdm.filter(F.a == "stats"))
async def cb_stats(callback: CallbackQuery, callback_data: BcAdm) -> None:
    info = await svc.get_broadcast(callback_data.id)
    if info is None:
        await render(callback, T.BC_NOT_FOUND)
        return
    await _progress(callback, info)


@admin_router.callback_query(BcAdm.filter(F.a == "cancel"))
async def cb_cancel(callback: CallbackQuery, callback_data: BcAdm) -> None:
    ok = await svc.cancel_broadcast(callback_data.id)
    await callback.answer(T.BC_CANCEL_SENT if ok else T.BC_NOT_RUNNING, show_alert=True)


@admin_router.callback_query(BcAdm.filter(F.a == "del"))
async def cb_delete(callback: CallbackQuery, callback_data: BcAdm) -> None:
    ok = await svc.delete_draft(callback_data.id)
    await callback.answer(T.BC_DELETED if ok else T.BC_ALREADY_RUNNING, show_alert=not ok)
    await render(callback, *V.listing(await svc.list_broadcasts(20)), answer_callback=False)


# =============================================================================
# 2.x commands
# =============================================================================


def _int_args(command: CommandObject) -> list[int]:
    return [int(x) for x in (command.args or "").split() if x.isdigit()]


@admin_router.message(Command("bc_preview"))
async def cmd_preview(message: Message, command: CommandObject) -> None:
    ids = _int_args(command)
    if not ids:
        await render(message, "Использование: /bc_preview &lt;id&gt;")
        return
    await _preview(message, ids[0], message.from_user.id)


@admin_router.message(Command("bc_send_to"))
async def cmd_send_to(message: Message, command: CommandObject) -> None:
    ids = _int_args(command)
    if len(ids) < 2:
        await render(message, "Использование: <code>/bc_send_to &lt;broadcast_id&gt; &lt;telegram_id&gt;</code>")
        return
    await _preview(message, ids[0], ids[1])
    await render(message, f"Отправлено в чат <code>{ids[1]}</code> (без записи в получателей).")


@admin_router.message(Command("bc_send"))
async def cmd_send(message: Message, command: CommandObject) -> None:
    ids = _int_args(command)
    if not ids:
        await render(message, "Использование: /bc_send &lt;id&gt;")
        return
    await _confirm(message, ids[0])


@admin_router.message(Command("bc_stats"))
async def cmd_stats(message: Message, command: CommandObject) -> None:
    ids = _int_args(command)
    info = await svc.get_broadcast(ids[0]) if ids else None
    if info is None:
        await render(message, T.BC_NOT_FOUND if ids else "Использование: /bc_stats &lt;id&gt;")
        return
    await _progress(message, info)


@admin_router.message(Command("bc_cancel"))
async def cmd_bc_cancel(message: Message, command: CommandObject) -> None:
    ids = _int_args(command)
    if not ids:
        await render(message, "Использование: /bc_cancel &lt;id&gt;")
        return
    await render(message, T.BC_CANCEL_SENT if await svc.cancel_broadcast(ids[0]) else T.BC_NOT_RUNNING)
