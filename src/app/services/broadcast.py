"""
Broadcast worker — рассылки админа по сегментам пользователей.

Ключевые свойства:
- Глобальный rate-limit 25 msg/sec (Telegram-лимит для ботов).
- Respect TelegramRetryAfter: asyncio.sleep(retry_after+1), до 3 ретраев на сообщение.
- TelegramForbiddenError → recipient.status=blocked, users.is_active=false, НЕ повторяем.
- TelegramBadRequest ("chat not found" / "user is deactivated") → blocked.
- Прочие исключения → failed, одна повторная попытка через 10с.
- Chunked fetch (500 штук за раз через ORDER BY id).
- Прогресс пишется в broadcast.{delivered,failed,blocked} каждые 50 сообщений.
- Resume на старте: broadcasts со started_at NOT NULL и finished_at NULL запускаются заново
  (recipient с status=sent пропускаются по UNIQUE-проверке + explicit `status!='sent'` фильтру).
- Graceful shutdown: `shutdown_broadcast_worker()` ждёт завершения in-flight задач.

НЕ делает:
- Не шлёт сообщения напрямую без записи в broadcast_recipient (нет слепых шлёпов).
- Не читает Remnawave — сегменты считаются по локальной БД (`subscriptions`, `payments`).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import Broadcast, BroadcastRecipient, Payment, Subscription, TelegramUser
from app.db.session import SessionLocal
from app.logger import logger


# Telegram API: ~30 msg/sec hard-limit для ботов; 25 даёт запас.
GLOBAL_RATE_LIMIT_PER_SEC = 25
SEND_INTERVAL = 1.0 / GLOBAL_RATE_LIMIT_PER_SEC  # ~0.04с

CHUNK_SIZE = 500
COMMIT_EVERY = 50
RETRY_AFTER_MAX_ATTEMPTS = 3
GENERIC_RETRY_DELAY = 10.0


SEGMENT_ALL = "all"
SEGMENT_ACTIVE = "active"
SEGMENT_EXPIRED = "expired"
SEGMENT_NEVER = "never"
VALID_SEGMENTS = {SEGMENT_ALL, SEGMENT_ACTIVE, SEGMENT_EXPIRED, SEGMENT_NEVER}


# Unsubscribe-кнопка, которую worker добавляет в каждое broadcast-сообщение.
UNSUB_CALLBACK_DATA = "bc:unsub"
UNSUB_BUTTON_TEXT = "🔕 Отписаться от рассылок"

# Close-кнопка — удаляет сообщение, чтобы юзер мог почистить чат.
CLOSE_CALLBACK_DATA = "bc:close"
CLOSE_BUTTON_TEXT = "❌ Закрыть"


# =============================================================================
# Worker registry (module-level) — чтобы graceful-shutdown мог дождаться всех задач.
# =============================================================================


@dataclass
class _WorkerHandle:
    broadcast_id: int
    task: asyncio.Task
    cancel_flag: asyncio.Event


_active_workers: dict[int, _WorkerHandle] = {}
_active_workers_lock = asyncio.Lock()


async def start_broadcast(bot: Bot, broadcast_id: int) -> bool:
    """Запускает worker для конкретной рассылки. Идемпотентно по broadcast_id."""
    async with _active_workers_lock:
        existing = _active_workers.get(broadcast_id)
        if existing and not existing.task.done():
            return False
        cancel_flag = asyncio.Event()
        task = asyncio.create_task(
            _run_worker(bot, broadcast_id, cancel_flag),
            name=f"broadcast-{broadcast_id}",
        )
        _active_workers[broadcast_id] = _WorkerHandle(broadcast_id, task, cancel_flag)
    return True


async def cancel_broadcast(broadcast_id: int) -> bool:
    """Посылает graceful-cancel текущему worker'у. In-flight сообщения дошлются."""
    async with _active_workers_lock:
        h = _active_workers.get(broadcast_id)
        if not h or h.task.done():
            return False
        h.cancel_flag.set()
    return True


async def shutdown_broadcast_worker() -> None:
    """Graceful shutdown — ставит cancel-флаг, ждёт завершения всех активных worker'ов."""
    async with _active_workers_lock:
        handles = list(_active_workers.values())
    for h in handles:
        h.cancel_flag.set()
    if handles:
        await asyncio.gather(*(h.task for h in handles), return_exceptions=True)


async def resume_unfinished_broadcasts(bot: Bot) -> int:
    """На старте: подхватить рассылки со started_at, но без finished_at."""
    if not SessionLocal:
        return 0
    async with SessionLocal() as session:
        stmt = select(Broadcast).where(
            Broadcast.started_at.isnot(None), Broadcast.finished_at.is_(None),
        )
        result = await session.execute(stmt)
        broadcasts = result.scalars().all()
    count = 0
    for bc in broadcasts:
        started = await start_broadcast(bot, bc.id)
        if started:
            count += 1
            logger.info(f"broadcast resume: id={bc.id} перезапущен после рестарта")
    return count


# =============================================================================
# Сегменты — SQL построитель. Возвращает выражение для WHERE по telegram_users.
# =============================================================================


def _base_audience_filter() -> Any:
    """Базовый фильтр: активный юзер и не отписан от рассылок."""
    return and_(
        TelegramUser.is_active.is_(True),
        TelegramUser.broadcast_opt_out.is_(False),
    )


def _segment_filter(segment: str) -> Any:
    """Возвращает SQL-условие для сегмента, применяемое к telegram_users."""
    base = _base_audience_filter()
    now = datetime.utcnow()

    if segment == SEGMENT_ALL:
        return base

    if segment == SEGMENT_ACTIVE:
        # Есть активная подписка с valid_until > now (или lifetime).
        active_sub_exists = exists().where(
            and_(
                Subscription.telegram_user_id == TelegramUser.telegram_id,
                Subscription.active.is_(True),
                or_(
                    Subscription.is_lifetime.is_(True),
                    Subscription.valid_until > now,
                ),
            )
        )
        return and_(base, active_sub_exists)

    if segment == SEGMENT_EXPIRED:
        # Была подписка (любая запись с valid_until), но сейчас нет активной в будущем.
        had_sub_exists = exists().where(Subscription.telegram_user_id == TelegramUser.telegram_id)
        active_sub_exists = exists().where(
            and_(
                Subscription.telegram_user_id == TelegramUser.telegram_id,
                Subscription.active.is_(True),
                or_(
                    Subscription.is_lifetime.is_(True),
                    Subscription.valid_until > now,
                ),
            )
        )
        return and_(base, had_sub_exists, ~active_sub_exists)

    if segment == SEGMENT_NEVER:
        # Юзер есть в БД, но ни одного успешного платежа.
        any_paid_exists = exists().where(
            and_(
                Payment.telegram_user_id == TelegramUser.telegram_id,
                Payment.status == "succeeded",
            )
        )
        return and_(base, ~any_paid_exists)

    raise ValueError(f"unknown segment: {segment!r}")


async def count_segment(segment: str) -> int:
    """Сколько юзеров попадает в сегмент. Используется при подтверждении /bc_send."""
    if segment not in VALID_SEGMENTS:
        raise ValueError(f"invalid segment: {segment}")
    if not SessionLocal:
        return 0
    async with SessionLocal() as session:
        stmt = select(func.count()).select_from(TelegramUser).where(_segment_filter(segment))
        return int((await session.execute(stmt)).scalar_one())


async def _iter_segment_user_ids(segment: str) -> list[list[int]]:
    """Возвращает список чанков по CHUNK_SIZE telegram_id. Всё на старте — проще для resume."""
    assert SessionLocal is not None
    async with SessionLocal() as session:
        stmt = (
            select(TelegramUser.telegram_id)
            .where(_segment_filter(segment))
            .order_by(TelegramUser.telegram_id)
        )
        result = await session.execute(stmt)
        all_ids = [row[0] for row in result.all()]
    chunks: list[list[int]] = []
    for i in range(0, len(all_ids), CHUNK_SIZE):
        chunks.append(all_ids[i : i + CHUNK_SIZE])
    return chunks


# =============================================================================
# Resolve recipients — materialize broadcast_recipients rows.
# =============================================================================


async def materialize_recipients(broadcast_id: int) -> int:
    """
    Создаёт broadcast_recipient(status='pending') для сегмента.
    UNIQUE(broadcast_id, user_telegram_id) + ON CONFLICT DO NOTHING — идемпотентно при рестартах.
    Возвращает общее число получателей после операции (broadcast.total).
    """
    assert SessionLocal is not None
    async with SessionLocal() as session:
        bc_result = await session.execute(select(Broadcast).where(Broadcast.id == broadcast_id))
        bc = bc_result.scalar_one_or_none()
        if not bc:
            raise ValueError(f"broadcast not found: id={broadcast_id}")
        segment = bc.segment

    chunks = await _iter_segment_user_ids(segment)

    inserted_total = 0
    async with SessionLocal() as session:
        for chunk in chunks:
            if not chunk:
                continue
            rows = [
                {"broadcast_id": broadcast_id, "user_telegram_id": tg_id, "status": "pending"}
                for tg_id in chunk
            ]
            stmt = pg_insert(BroadcastRecipient).values(rows).on_conflict_do_nothing(
                index_elements=["broadcast_id", "user_telegram_id"]
            )
            await session.execute(stmt)
            inserted_total += len(rows)
        # total — фактическое число recipient-записей (на случай повторного materialize).
        total_q = await session.execute(
            select(func.count()).select_from(BroadcastRecipient).where(
                BroadcastRecipient.broadcast_id == broadcast_id
            )
        )
        total = int(total_q.scalar_one())
        await session.execute(
            update(Broadcast).where(Broadcast.id == broadcast_id).values(total=total)
        )
        await session.commit()
    return total


# =============================================================================
# Worker loop
# =============================================================================


def _attach_unsub_button(buttons_json: Optional[list[dict]]) -> InlineKeyboardMarkup:
    """Собирает клавиатуру из buttons_json + строка «Отписаться» / «Закрыть»."""
    rows: list[list[InlineKeyboardButton]] = []
    for btn in (buttons_json or []):
        text = btn.get("text") or "→"
        if btn.get("url"):
            rows.append([InlineKeyboardButton(text=text, url=btn["url"])])
        elif btn.get("callback_data"):
            rows.append([InlineKeyboardButton(text=text, callback_data=btn["callback_data"])])
    # Системная строка: «Отписаться» и «Закрыть» рядом.
    rows.append([
        InlineKeyboardButton(text=UNSUB_BUTTON_TEXT, callback_data=UNSUB_CALLBACK_DATA),
        InlineKeyboardButton(text=CLOSE_BUTTON_TEXT, callback_data=CLOSE_CALLBACK_DATA),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_one(
    bot: Bot,
    user_id: int,
    text_html: str,
    photo_file_id: Optional[str],
    reply_markup: InlineKeyboardMarkup,
    disable_notification: bool,
) -> None:
    """Один send с respect TelegramRetryAfter (до 3 попыток)."""
    for attempt in range(RETRY_AFTER_MAX_ATTEMPTS):
        try:
            if photo_file_id:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file_id,
                    caption=text_html,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                )
            else:
                await bot.send_message(
                    chat_id=user_id,
                    text=text_html,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                )
            return
        except TelegramRetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(f"broadcast RetryAfter: user={user_id} wait={wait}s attempt={attempt+1}")
            await asyncio.sleep(wait)
    raise TimeoutError("RetryAfter exhausted")


async def _mark_user_inactive(session, user_id: int) -> None:
    await session.execute(
        update(TelegramUser).where(TelegramUser.telegram_id == user_id).values(is_active=False)
    )


async def _update_recipient(
    session, broadcast_id: int, user_id: int, *, status: str, error_text: Optional[str],
) -> None:
    vals: dict[str, Any] = {"status": status, "sent_at": datetime.utcnow()}
    if error_text is not None:
        vals["error_text"] = error_text[:500]
    await session.execute(
        update(BroadcastRecipient)
        .where(
            and_(
                BroadcastRecipient.broadcast_id == broadcast_id,
                BroadcastRecipient.user_telegram_id == user_id,
            )
        )
        .values(**vals)
    )


async def _run_worker(bot: Bot, broadcast_id: int, cancel_flag: asyncio.Event) -> None:
    """Рабочий цикл рассылки. Читает pending recipient-ов и шлёт по SEND_INTERVAL."""
    logger.info(f"broadcast worker start: id={broadcast_id}")
    if not SessionLocal:
        logger.error(f"broadcast worker: SessionLocal=None, abort id={broadcast_id}")
        return

    # Снимаем снапшот broadcast и материализуем получателей, если ещё не.
    async with SessionLocal() as session:
        bc = await session.get(Broadcast, broadcast_id)
        if not bc:
            logger.error(f"broadcast worker: not found id={broadcast_id}")
            return
        if bc.started_at is None:
            bc.started_at = datetime.utcnow()
            await session.commit()
        text_html = bc.text_html
        photo_file_id = bc.photo_file_id
        buttons_json = bc.buttons_json
        disable_notification = bc.disable_notification

    try:
        await materialize_recipients(broadcast_id)
    except Exception as e:
        logger.error(f"broadcast materialize failed: id={broadcast_id} err={e}")
        return

    reply_markup = _attach_unsub_button(buttons_json)
    delivered = failed = blocked = 0
    since_commit = 0
    last_send = 0.0

    while not cancel_flag.is_set():
        async with SessionLocal() as session:
            stmt = (
                select(BroadcastRecipient)
                .where(
                    BroadcastRecipient.broadcast_id == broadcast_id,
                    BroadcastRecipient.status == "pending",
                )
                .order_by(BroadcastRecipient.id)
                .limit(CHUNK_SIZE)
            )
            chunk = (await session.execute(stmt)).scalars().all()

        if not chunk:
            break

        for rec in chunk:
            if cancel_flag.is_set():
                break
            # Rate-limit per-tick: ждать до next send-slot.
            now = asyncio.get_event_loop().time()
            elapsed = now - last_send
            if elapsed < SEND_INTERVAL:
                await asyncio.sleep(SEND_INTERVAL - elapsed)
            last_send = asyncio.get_event_loop().time()

            user_id = rec.user_telegram_id
            status: str
            error_text: Optional[str] = None
            should_mark_inactive = False

            try:
                await _send_one(
                    bot,
                    user_id,
                    text_html=text_html,
                    photo_file_id=photo_file_id,
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                )
                status = "sent"
                delivered += 1
            except TelegramForbiddenError as e:
                status = "blocked"
                blocked += 1
                error_text = f"forbidden: {e}"
                should_mark_inactive = True
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if any(s in msg for s in ("chat not found", "user is deactivated", "bot was blocked")):
                    status = "blocked"
                    blocked += 1
                    should_mark_inactive = True
                else:
                    status = "failed"
                    failed += 1
                error_text = str(e)[:500]
            except (TelegramNetworkError, TimeoutError, asyncio.TimeoutError) as e:
                # Один повторный шанс через 10с.
                await asyncio.sleep(GENERIC_RETRY_DELAY)
                try:
                    await _send_one(
                        bot,
                        user_id,
                        text_html=text_html,
                        photo_file_id=photo_file_id,
                        reply_markup=reply_markup,
                        disable_notification=disable_notification,
                    )
                    status = "sent"
                    delivered += 1
                except Exception as e2:
                    status = "failed"
                    failed += 1
                    error_text = f"retry_failed: {e2}"
            except Exception as e:
                status = "failed"
                failed += 1
                error_text = f"unexpected: {e}"

            async with SessionLocal() as session:
                await _update_recipient(
                    session, broadcast_id, user_id, status=status, error_text=error_text,
                )
                if should_mark_inactive:
                    await _mark_user_inactive(session, user_id)
                await session.commit()

            since_commit += 1
            if since_commit >= COMMIT_EVERY:
                async with SessionLocal() as session:
                    await session.execute(
                        update(Broadcast).where(Broadcast.id == broadcast_id).values(
                            delivered=Broadcast.delivered + delivered,
                            failed=Broadcast.failed + failed,
                            blocked=Broadcast.blocked + blocked,
                        )
                    )
                    await session.commit()
                delivered = failed = blocked = 0
                since_commit = 0

    # Финальный flush + finished_at (если не было cancel).
    async with SessionLocal() as session:
        vals: dict[str, Any] = {
            "delivered": Broadcast.delivered + delivered,
            "failed": Broadcast.failed + failed,
            "blocked": Broadcast.blocked + blocked,
        }
        if not cancel_flag.is_set():
            vals["finished_at"] = datetime.utcnow()
        await session.execute(
            update(Broadcast).where(Broadcast.id == broadcast_id).values(**vals)
        )
        await session.commit()

    async with _active_workers_lock:
        _active_workers.pop(broadcast_id, None)

    reason = "cancelled" if cancel_flag.is_set() else "finished"
    logger.info(f"broadcast worker {reason}: id={broadcast_id}")
