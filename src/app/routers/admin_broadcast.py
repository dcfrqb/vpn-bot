"""
Admin-broadcast FSM wizard + команды управления рассылками.

Команды (только для admin_id из settings.ADMINS):
  /bc_new          — создать черновик (FSM: текст → фото → кнопки → сегмент → notify → превью).
  /bc_preview <id> — отправить черновик только самому админу (без записи в recipient).
  /bc_list         — список последних 20 рассылок.
  /bc_send <id>    — запуск рассылки (требует подтверждения).
  /bc_stats <id>   — прогресс.
  /bc_cancel <id>  — остановить running рассылку (graceful).

Юзерские команды (регистрируются здесь, т.к. связаны с broadcast):
  /stop            — opt-out из всех будущих рассылок.
  callback `bc:unsub` — то же, кнопкой в самой рассылке.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, update

from app.config import is_admin
from app.db.models import Broadcast, TelegramUser
from app.db.session import SessionLocal
from app.logger import logger
from app.services.broadcast import (
    SEGMENT_ACTIVE,
    SEGMENT_ALL,
    SEGMENT_EXPIRED,
    SEGMENT_NEVER,
    UNSUB_CALLBACK_DATA,
    VALID_SEGMENTS,
    cancel_broadcast,
    count_segment,
    start_broadcast,
)


router = Router(name="admin_broadcast")


# =============================================================================
# Юзерский opt-out: /stop + callback bc:unsub
# =============================================================================


async def _set_opt_out(user_id: int, value: bool) -> None:
    if not SessionLocal:
        return
    async with SessionLocal() as session:
        await session.execute(
            update(TelegramUser)
            .where(TelegramUser.telegram_id == user_id)
            .values(broadcast_opt_out=value)
        )
        await session.commit()


@router.message(Command("stop"))
async def cmd_stop(message: types.Message) -> None:
    """Отписка от рассылок. НЕ блокирует транзакционные уведомления (оплата и т.п.)."""
    user_id = message.from_user.id
    await _set_opt_out(user_id, True)
    await message.answer(
        "🔕 Вы отписаны от рассылок.\n\n"
        "Транзакционные уведомления (об оплате, активации подписки) продолжат приходить.\n"
        "Чтобы снова подписаться — команда /start.",
    )


async def reset_broadcast_opt_out(user_id: int) -> None:
    """Хук для /start: снять opt_out при явном re-engage.
    Вызывается из routers/start.py чтобы не конфликтовать с основным CommandStart хендлером.
    """
    try:
        await _set_opt_out(user_id, False)
    except Exception as e:
        logger.debug(f"reset_broadcast_opt_out soft-fail: {e}")


@router.callback_query(F.data == UNSUB_CALLBACK_DATA)
async def cb_unsub(callback: types.CallbackQuery) -> None:
    await _set_opt_out(callback.from_user.id, True)
    await callback.answer("🔕 Вы отписаны от рассылок")
    # В самой рассылке пытаемся убрать клавиатуру, чтобы кнопка не торчала.
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# =============================================================================
# FSM wizard /bc_new
# =============================================================================


class BroadcastDraft(StatesGroup):
    WAIT_TEXT = State()
    WAIT_PHOTO = State()
    WAIT_BUTTONS = State()
    WAIT_SEGMENT = State()
    WAIT_NOTIFY = State()
    PREVIEW = State()


def _admin_only(message_or_callback) -> bool:
    user = getattr(message_or_callback, "from_user", None)
    return bool(user and is_admin(user.id))


@router.message(Command("bc_new"))
async def cmd_bc_new(message: types.Message, state: FSMContext) -> None:
    if not _admin_only(message):
        return
    await state.clear()
    await state.set_state(BroadcastDraft.WAIT_TEXT)
    await message.answer(
        "📢 <b>Новая рассылка — шаг 1/5</b>\n\n"
        "Отправьте текст сообщения (HTML-разметка поддерживается: &lt;b&gt;, &lt;i&gt;, &lt;a&gt;, &lt;code&gt;, &lt;blockquote&gt;).\n\n"
        "Отмена: /cancel",
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext) -> None:
    if not _admin_only(message):
        return
    current = await state.get_state()
    if not current:
        return
    await state.clear()
    await message.answer("❌ Создание рассылки отменено")


@router.message(BroadcastDraft.WAIT_TEXT, F.text)
async def bc_step_text(message: types.Message, state: FSMContext) -> None:
    if not _admin_only(message):
        return
    text = message.html_text or message.text or ""
    if not text.strip():
        await message.answer("Пустой текст. Пришлите непустое сообщение.")
        return
    if len(text) > 4000:
        await message.answer(f"Слишком длинный текст ({len(text)} симв., лимит Telegram ~4096).")
        return
    await state.update_data(text_html=text)
    await state.set_state(BroadcastDraft.WAIT_PHOTO)
    await message.answer(
        "📷 <b>Шаг 2/5 — фото</b>\n\n"
        "Отправьте фото, или напишите <code>пропустить</code> чтобы без фото.",
        parse_mode="HTML",
    )


@router.message(BroadcastDraft.WAIT_PHOTO, F.photo)
async def bc_step_photo(message: types.Message, state: FSMContext) -> None:
    if not _admin_only(message):
        return
    file_id = message.photo[-1].file_id  # самое большое разрешение
    await state.update_data(photo_file_id=file_id)
    await _ask_buttons(message, state)


@router.message(BroadcastDraft.WAIT_PHOTO, F.text.casefold() == "пропустить")
async def bc_step_photo_skip(message: types.Message, state: FSMContext) -> None:
    if not _admin_only(message):
        return
    await state.update_data(photo_file_id=None)
    await _ask_buttons(message, state)


async def _ask_buttons(message: types.Message, state: FSMContext) -> None:
    await state.set_state(BroadcastDraft.WAIT_BUTTONS)
    await message.answer(
        "🔘 <b>Шаг 3/5 — кнопки</b>\n\n"
        "Отправьте JSON-массив кнопок или <code>пропустить</code>.\n\n"
        "Пример: <code>[{\"text\": \"Открыть сайт\", \"url\": \"https://example.com\"}]</code>\n\n"
        "Поддерживаются поля: <code>text</code> + <code>url</code> ИЛИ <code>text</code> + <code>callback_data</code>.\n"
        "Каждая кнопка — отдельной строкой в клавиатуре. Кнопка «Отписаться» добавляется автоматически.",
        parse_mode="HTML",
    )


@router.message(BroadcastDraft.WAIT_BUTTONS, F.text.casefold() == "пропустить")
async def bc_step_buttons_skip(message: types.Message, state: FSMContext) -> None:
    if not _admin_only(message):
        return
    await state.update_data(buttons_json=None)
    await _ask_segment(message, state)


@router.message(BroadcastDraft.WAIT_BUTTONS, F.text)
async def bc_step_buttons(message: types.Message, state: FSMContext) -> None:
    if not _admin_only(message):
        return
    try:
        parsed = json.loads(message.text)
        if not isinstance(parsed, list):
            raise ValueError("ожидается массив кнопок")
        for item in parsed:
            if not isinstance(item, dict) or "text" not in item:
                raise ValueError("каждая кнопка — dict с обязательным 'text'")
            if not (item.get("url") or item.get("callback_data")):
                raise ValueError("каждой кнопке нужен 'url' или 'callback_data'")
    except Exception as e:
        await message.answer(f"❌ Невалидный JSON кнопок: {e}\n\nПопробуйте снова или /cancel.")
        return
    await state.update_data(buttons_json=parsed)
    await _ask_segment(message, state)


async def _ask_segment(message: types.Message, state: FSMContext) -> None:
    await state.set_state(BroadcastDraft.WAIT_SEGMENT)
    await message.answer(
        "👥 <b>Шаг 4/5 — сегмент</b>\n\n"
        "Кому шлём?\n"
        "• <code>all</code> — все активные юзеры не в opt-out\n"
        "• <code>active</code> — с активной подпиской\n"
        "• <code>expired</code> — были, но истекли\n"
        "• <code>never</code> — никогда не платили\n\n"
        "Пришлите одно из четырёх значений.",
        parse_mode="HTML",
    )


@router.message(BroadcastDraft.WAIT_SEGMENT, F.text)
async def bc_step_segment(message: types.Message, state: FSMContext) -> None:
    if not _admin_only(message):
        return
    segment = (message.text or "").strip().lower()
    if segment not in VALID_SEGMENTS:
        await message.answer(f"❌ Неизвестный сегмент. Используйте: {', '.join(sorted(VALID_SEGMENTS))}.")
        return
    await state.update_data(segment=segment)
    await state.set_state(BroadcastDraft.WAIT_NOTIFY)
    await message.answer(
        "🔔 <b>Шаг 5/5 — звук</b>\n\n"
        "Слать со звуком?\n"
        "• <code>да</code> — со звуком (важные уведомления: expired/never)\n"
        "• <code>нет</code> — тихо (инфо для active)\n\n"
        "По умолчанию для <code>active</code> — тихо, для <code>expired/never</code> — со звуком.",
        parse_mode="HTML",
    )


@router.message(BroadcastDraft.WAIT_NOTIFY, F.text)
async def bc_step_notify(message: types.Message, state: FSMContext) -> None:
    if not _admin_only(message):
        return
    raw = (message.text or "").strip().lower()
    if raw in ("да", "yes", "y", "true", "1"):
        disable_notification = False
    elif raw in ("нет", "no", "n", "false", "0"):
        disable_notification = True
    elif raw in ("по умолчанию", "default"):
        data = await state.get_data()
        disable_notification = data.get("segment") == SEGMENT_ACTIVE
    else:
        await message.answer("Ответьте «да», «нет» или «по умолчанию».")
        return
    await state.update_data(disable_notification=disable_notification)

    # Создаём черновик в БД.
    data = await state.get_data()
    if not SessionLocal:
        await message.answer("❌ БД не настроена")
        await state.clear()
        return
    async with SessionLocal() as session:
        bc = Broadcast(
            text_html=data["text_html"],
            photo_file_id=data.get("photo_file_id"),
            buttons_json=data.get("buttons_json"),
            segment=data["segment"],
            disable_notification=disable_notification,
            created_by=message.from_user.id,
        )
        session.add(bc)
        await session.commit()
        await session.refresh(bc)

    audience = await count_segment(data["segment"])
    await state.clear()
    await message.answer(
        f"✅ <b>Черновик создан: ID={bc.id}</b>\n\n"
        f"Сегмент: <code>{data['segment']}</code> (≈{audience} получателей)\n"
        f"Фото: {'да' if data.get('photo_file_id') else 'нет'}\n"
        f"Кнопок: {len(data.get('buttons_json') or [])}\n"
        f"Звук: {'выкл' if disable_notification else 'вкл'}\n\n"
        f"Команды:\n"
        f"• <code>/bc_preview {bc.id}</code> — отправить себе\n"
        f"• <code>/bc_send {bc.id}</code> — запустить реальную рассылку\n"
        f"• <code>/bc_list</code> — список всех",
        parse_mode="HTML",
    )


# =============================================================================
# /bc_preview
# =============================================================================


def _parse_int_arg(message: types.Message) -> Optional[int]:
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return None
    try:
        return int(parts[1].strip())
    except ValueError:
        return None


@router.message(Command("bc_preview"))
async def cmd_bc_preview(message: types.Message) -> None:
    if not _admin_only(message):
        return
    bc_id = _parse_int_arg(message)
    if bc_id is None:
        await message.answer("Использование: /bc_preview <id>")
        return
    if not SessionLocal:
        await message.answer("❌ БД не настроена")
        return
    async with SessionLocal() as session:
        bc = await session.get(Broadcast, bc_id)
    if not bc:
        await message.answer(f"Рассылка {bc_id} не найдена")
        return

    from app.services.broadcast import _attach_unsub_button  # re-use keyboard builder

    reply_markup = _attach_unsub_button(bc.buttons_json)
    try:
        if bc.photo_file_id:
            await message.bot.send_photo(
                chat_id=message.from_user.id,
                photo=bc.photo_file_id,
                caption=bc.text_html,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_notification=bc.disable_notification,
            )
        else:
            await message.bot.send_message(
                chat_id=message.from_user.id,
                text=bc.text_html,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_notification=bc.disable_notification,
            )
    except Exception as e:
        await message.answer(f"❌ Ошибка превью: {e}")


# =============================================================================
# /bc_list
# =============================================================================


@router.message(Command("bc_list"))
async def cmd_bc_list(message: types.Message) -> None:
    if not _admin_only(message):
        return
    if not SessionLocal:
        await message.answer("❌ БД не настроена")
        return
    async with SessionLocal() as session:
        result = await session.execute(
            select(Broadcast).order_by(Broadcast.id.desc()).limit(20)
        )
        rows = result.scalars().all()
    if not rows:
        await message.answer("Черновиков/рассылок нет")
        return
    lines = ["<b>Рассылки (последние 20):</b>"]
    for bc in rows:
        if bc.finished_at:
            state = "✅ done"
        elif bc.started_at:
            state = "🟡 running"
        else:
            state = "📝 draft"
        lines.append(
            f"#{bc.id} {state} seg=<code>{bc.segment}</code> "
            f"total={bc.total} ok={bc.delivered} fail={bc.failed} blk={bc.blocked}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


# =============================================================================
# /bc_send
# =============================================================================


# Ключ — admin_id, значение — pending broadcast_id для подтверждения.
_pending_confirmations: dict[int, int] = {}


@router.message(Command("bc_send"))
async def cmd_bc_send(message: types.Message) -> None:
    if not _admin_only(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: /bc_send <id> [confirm]")
        return
    try:
        bc_id = int(parts[1])
    except ValueError:
        await message.answer("id должен быть числом")
        return
    is_confirm = len(parts) >= 3 and parts[2].lower() in ("confirm", "да", "yes")

    if not SessionLocal:
        await message.answer("❌ БД не настроена")
        return
    async with SessionLocal() as session:
        bc = await session.get(Broadcast, bc_id)
    if not bc:
        await message.answer(f"Рассылка {bc_id} не найдена")
        return
    if bc.started_at and not bc.finished_at:
        await message.answer(f"Рассылка {bc_id} уже запущена. /bc_stats {bc_id}")
        return
    if bc.finished_at:
        await message.answer(f"Рассылка {bc_id} уже завершена. /bc_stats {bc_id}")
        return

    if not is_confirm:
        audience = await count_segment(bc.segment)
        _pending_confirmations[message.from_user.id] = bc_id
        await message.answer(
            f"⚠️ Подтверждение: рассылка <b>#{bc_id}</b>, сегмент <code>{bc.segment}</code>, "
            f"≈<b>{audience}</b> получателей.\n\n"
            f"Отправьте <code>/bc_send {bc_id} confirm</code> чтобы запустить.",
            parse_mode="HTML",
        )
        return

    # Защита: confirm без предварительного вызова — отказ.
    if _pending_confirmations.get(message.from_user.id) != bc_id:
        await message.answer(
            "Подтверждение не найдено. Сначала вызовите /bc_send <id> без confirm."
        )
        return
    _pending_confirmations.pop(message.from_user.id, None)

    started = await start_broadcast(message.bot, bc_id)
    if started:
        await message.answer(f"🚀 Рассылка #{bc_id} запущена. /bc_stats {bc_id} для прогресса.")
    else:
        await message.answer(f"Рассылка #{bc_id} уже running.")


# =============================================================================
# /bc_send_to — прицельная отправка конкретному пользователю
# =============================================================================


@router.message(Command("bc_send_to"))
async def cmd_bc_send_to(message: types.Message) -> None:
    """Отправка черновика конкретному telegram_id минуя сегмент.

    Usage: /bc_send_to <broadcast_id> <telegram_id>

    Использует ту же логику, что и worker: добавляет кнопку «Отписаться»,
    respect photo/buttons/disable_notification. НЕ пишет recipient-запись
    (это разовый тестовый send, не часть массовой рассылки).
    Игнорирует is_active/opt_out — админ решает куда тестить.
    """
    if not _admin_only(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer(
            "Использование: <code>/bc_send_to &lt;broadcast_id&gt; &lt;telegram_id&gt;</code>",
            parse_mode="HTML",
        )
        return
    try:
        bc_id = int(parts[1])
        target_id = int(parts[2])
    except ValueError:
        await message.answer("broadcast_id и telegram_id должны быть числами")
        return

    if not SessionLocal:
        await message.answer("❌ БД не настроена")
        return
    async with SessionLocal() as session:
        bc = await session.get(Broadcast, bc_id)
    if not bc:
        await message.answer(f"Рассылка {bc_id} не найдена")
        return

    from app.services.broadcast import _attach_unsub_button

    reply_markup = _attach_unsub_button(bc.buttons_json)
    try:
        if bc.photo_file_id:
            await message.bot.send_photo(
                chat_id=target_id,
                photo=bc.photo_file_id,
                caption=bc.text_html,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_notification=bc.disable_notification,
            )
        else:
            await message.bot.send_message(
                chat_id=target_id,
                text=bc.text_html,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_notification=bc.disable_notification,
            )
        await message.answer(f"✅ Отправлено в чат <code>{target_id}</code>", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки в {target_id}: {e}")


# =============================================================================
# /bc_stats
# =============================================================================


@router.message(Command("bc_stats"))
async def cmd_bc_stats(message: types.Message) -> None:
    if not _admin_only(message):
        return
    bc_id = _parse_int_arg(message)
    if bc_id is None:
        await message.answer("Использование: /bc_stats <id>")
        return
    if not SessionLocal:
        await message.answer("❌ БД не настроена")
        return
    async with SessionLocal() as session:
        bc = await session.get(Broadcast, bc_id)
    if not bc:
        await message.answer(f"Рассылка {bc_id} не найдена")
        return
    if bc.finished_at:
        state = "✅ finished"
    elif bc.started_at:
        state = "🟡 running"
    else:
        state = "📝 draft"
    processed = bc.delivered + bc.failed + bc.blocked
    pct = (processed * 100 // bc.total) if bc.total else 0
    text = (
        f"<b>Рассылка #{bc.id} — {state}</b>\n\n"
        f"Сегмент: <code>{bc.segment}</code>\n"
        f"Total: {bc.total}\n"
        f"Delivered: {bc.delivered}\n"
        f"Failed: {bc.failed}\n"
        f"Blocked: {bc.blocked}\n"
        f"Progress: {processed}/{bc.total} ({pct}%)\n"
    )
    if bc.started_at:
        text += f"Started: {bc.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
    if bc.finished_at:
        text += f"Finished: {bc.finished_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
    await message.answer(text, parse_mode="HTML")


# =============================================================================
# /bc_cancel
# =============================================================================


@router.message(Command("bc_cancel"))
async def cmd_bc_cancel(message: types.Message) -> None:
    if not _admin_only(message):
        return
    bc_id = _parse_int_arg(message)
    if bc_id is None:
        await message.answer("Использование: /bc_cancel <id>")
        return
    ok = await cancel_broadcast(bc_id)
    if ok:
        await message.answer(f"🛑 Cancel отправлен для #{bc_id}. In-flight сообщения дойдут.")
    else:
        await message.answer(f"Рассылка #{bc_id} не running")
