"""Broadcasts (stream E, release 3.0; 2.x worker kept compatible).

Key properties (2.x, unchanged):
- global rate limit 25 msg/s; RetryAfter respected; blocked users are
  marked inactive and never retried (classification lives in the sender);
- recipients are materialized into ``broadcast_recipients`` (unique per
  broadcast and user, ON CONFLICT DO NOTHING), so a restart resumes and never
  sends twice (status 'sent' rows are skipped);
- progress counters flushed every 50 messages; graceful cancel/shutdown.

New in 3.0:
- segments with parameters (``Segment``, stored in ``broadcasts.segment`` +
  ``segment_params``): all | active | expired | never | trial_nc | ids, with
  ``sub_kind`` (default "main") and "within N days" for active/expired/trial;
- ``credit_days``: every delivered recipient gets +N days via
  ProvisioningService, idempotent per (broadcast, user): a ledger row
  ``promo_redemptions(code='bc:<id>')`` is written before the grant and the
  grant uses the deterministic trace_id ``bc:<id>:<user>``; a final sweep
  credits recipients whose credit was interrupted by a restart;
- the Telegram side is a ``BroadcastSender`` (app.bot.broadcast_sender); this
  module imports no aiogram.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional, Protocol

from app.logger import logger
from app.services.broadcast_segments import (  # noqa: F401 - re-exported (2.x names)
    MAX_IDS,
    SEGMENT_ACTIVE,
    SEGMENT_ALL,
    SEGMENT_EXPIRED,
    SEGMENT_IDS,
    SEGMENT_NEVER,
    SEGMENT_TRIAL_NC,
    SEGMENTS,
    SUB_KINDS,
    VALID_SEGMENTS,
    Segment,
    _segment_filter,
    _segment_user_ids,
    count_segment,
    segment_filter,
)
from app.services.broadcast_drafts import (  # noqa: F401 - re-exported
    BroadcastInfo,
    create_draft,
    delete_draft,
    get_broadcast,
    list_broadcasts,
    set_opt_out,
)

GLOBAL_RATE_LIMIT_PER_SEC = 25
SEND_INTERVAL = 1.0 / GLOBAL_RATE_LIMIT_PER_SEC
CHUNK_SIZE = 500
COMMIT_EVERY = 50
RETRY_AFTER_MAX_ATTEMPTS = 3
GENERIC_RETRY_DELAY = 10.0

# Buttons under every broadcast message (bytes identical to 2.x; Bc callbacks).
UNSUB_CALLBACK_DATA = "bc:unsub"
UNSUB_BUTTON_TEXT = "🔕 Отписаться от рассылок"
CLOSE_CALLBACK_DATA = "bc:close"
CLOSE_BUTTON_TEXT = "❌ Закрыть"

CreditFn = Callable[[int, int, int], Awaitable[str]]


# =============================================================================
# Sender port (implemented in app.bot.broadcast_sender)
# =============================================================================


@dataclass(frozen=True)
class SendResult:
    status: str  # sent | blocked | failed
    error: Optional[str] = None


class BroadcastSender(Protocol):
    async def send(self, user_id: int, *, text_html: str, photo_file_id: Optional[str],
                   buttons: Optional[list[dict]], disable_notification: bool) -> SendResult: ...


def _sender_for(bot_or_sender: Any) -> BroadcastSender:
    if hasattr(bot_or_sender, "send") and not hasattr(bot_or_sender, "send_message"):
        return bot_or_sender
    from app.bot.broadcast_sender import AiogramBroadcastSender

    return AiogramBroadcastSender(bot_or_sender)


def _attach_unsub_button(buttons_json: Optional[list[dict]]) -> Any:
    """2.x name: keyboard of a broadcast message (built in the bot layer)."""
    from app.bot.broadcast_sender import build_markup

    return build_markup(buttons_json)


# =============================================================================
# Credits (credit_days)
# =============================================================================


def credit_code(broadcast_id: int) -> str:
    return f"bc:{int(broadcast_id)}"


async def credit_one(ledger: Any, add_days_fn: Callable[..., Awaitable[Any]], broadcast_id: int,
                     user_id: int, days: int) -> str:
    """+days for one recipient, at most once per (broadcast, user).

    applied/skipped in the ledger -> "dup"; otherwise the ledger row goes to
    pending BEFORE the grant (record-first) and the grant carries the
    deterministic trace_id, so a replay after a crash between the two is
    absorbed by ProvisioningService idempotency."""
    code = credit_code(broadcast_id)
    if await ledger.state(code, user_id) in ("applied", "skipped"):
        return "dup"
    await ledger.open(code, user_id, {"days": int(days), "broadcast_id": int(broadcast_id)})
    try:
        res = await add_days_fn(int(user_id), int(days), trace_id=f"bc:{int(broadcast_id)}:{int(user_id)}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"broadcast credit bc={broadcast_id} user={user_id} failed ({type(e).__name__})")
        await ledger.close(code, user_id, "failed")
        return "failed"
    if res is None:
        await ledger.close(code, user_id, "skipped")
        return "skipped"
    await ledger.close(code, user_id, "applied")
    return "applied"


class BroadcastCredits:
    """CreditFn over ProvisioningService (via grants.add_days) and the ledger."""

    def __init__(self, *, provisioning: Any, status: Any, ledger: Any = None):
        from app.services.grants import SqlRedemptionLedger

        self.provisioning = provisioning
        self.status = status
        self.ledger = ledger or SqlRedemptionLedger()

    async def _add(self, user_id: int, days: int, *, trace_id: str):
        from app.domain.models import EntitlementSource
        from app.services.grants import add_days

        return await add_days(self.provisioning, self.status, user_id, days, trace_id=trace_id,
                              source=EntitlementSource.PROMO)

    async def __call__(self, broadcast_id: int, user_id: int, days: int) -> str:
        return await credit_one(self.ledger, self._add, broadcast_id, user_id, days)


def credits_from_container(container: Any) -> Optional[BroadcastCredits]:
    if container is None:
        return None
    return BroadcastCredits(provisioning=container.provisioning, status=container.status)


# =============================================================================
# Worker registry
# =============================================================================


@dataclass
class _WorkerHandle:
    broadcast_id: int
    task: asyncio.Task
    cancel_flag: asyncio.Event
    stats: dict = field(default_factory=dict)


_active_workers: dict[int, _WorkerHandle] = {}
_active_workers_lock = asyncio.Lock()


async def start_broadcast(bot_or_sender: Any, broadcast_id: int, *, credit: Optional[CreditFn] = None) -> bool:
    """Start the worker of one broadcast. Idempotent per broadcast_id.

    ``credit`` is required when the broadcast has credit_days; when omitted
    it is built from the process container (app.container.get_container)."""
    sender = _sender_for(bot_or_sender)
    if credit is None:
        try:
            from app.container import get_container

            credit = credits_from_container(get_container())
        except RuntimeError:
            credit = None
    async with _active_workers_lock:
        existing = _active_workers.get(broadcast_id)
        if existing and not existing.task.done():
            return False
        cancel_flag = asyncio.Event()
        task = asyncio.create_task(_run_worker(sender, broadcast_id, cancel_flag, credit), name=f"broadcast-{broadcast_id}")
        _active_workers[broadcast_id] = _WorkerHandle(broadcast_id, task, cancel_flag)
    return True


def is_running(broadcast_id: int) -> bool:
    h = _active_workers.get(int(broadcast_id))
    return bool(h and not h.task.done())


async def cancel_broadcast(broadcast_id: int) -> bool:
    async with _active_workers_lock:
        h = _active_workers.get(broadcast_id)
        if not h or h.task.done():
            return False
        h.cancel_flag.set()
    return True


async def shutdown_broadcast_worker() -> None:
    async with _active_workers_lock:
        handles = list(_active_workers.values())
    for h in handles:
        h.cancel_flag.set()
    if handles:
        await asyncio.gather(*(h.task for h in handles), return_exceptions=True)


async def resume_unfinished_broadcasts(bot_or_sender: Any, *, credit: Optional[CreditFn] = None) -> int:
    from sqlalchemy import select

    from app.db.models import Broadcast
    from app.db.session import SessionLocal

    if not SessionLocal:
        return 0
    async with SessionLocal() as session:
        rows = (await session.execute(select(Broadcast.id).where(
            Broadcast.started_at.isnot(None), Broadcast.finished_at.is_(None)))).all()
    count = 0
    for (bid,) in rows:
        if await start_broadcast(bot_or_sender, bid, credit=credit):
            count += 1
            logger.info(f"broadcast resume: id={bid}")
    return count


# =============================================================================
# Recipients + worker loop
# =============================================================================


async def materialize_recipients(broadcast_id: int) -> int:
    from sqlalchemy import func, select, update
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.models import Broadcast, BroadcastRecipient
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        bc = await session.get(Broadcast, broadcast_id)
        if not bc:
            raise ValueError(f"broadcast not found: id={broadcast_id}")
        seg = Segment.from_row(bc.segment, bc.segment_params)
    ids = await _segment_user_ids(seg)
    async with SessionLocal() as session:
        for i in range(0, len(ids), CHUNK_SIZE):
            chunk = ids[i:i + CHUNK_SIZE]
            rows = [{"broadcast_id": broadcast_id, "user_telegram_id": tg, "status": "pending"} for tg in chunk]
            await session.execute(pg_insert(BroadcastRecipient).values(rows).on_conflict_do_nothing(
                index_elements=["broadcast_id", "user_telegram_id"]))
        total = int((await session.execute(select(func.count()).select_from(BroadcastRecipient).where(
            BroadcastRecipient.broadcast_id == broadcast_id))).scalar_one())
        await session.execute(update(Broadcast).where(Broadcast.id == broadcast_id).values(total=total))
        await session.commit()
    return total


async def _flush(broadcast_id: int, delivered: int, failed: int, blocked: int, *, finished: bool) -> None:
    from sqlalchemy import update

    from app.db.models import Broadcast
    from app.db.session import SessionLocal

    vals: dict[str, Any] = {"delivered": Broadcast.delivered + delivered, "failed": Broadcast.failed + failed,
                            "blocked": Broadcast.blocked + blocked}
    if finished:
        vals["finished_at"] = datetime.utcnow()
    async with SessionLocal() as session:
        await session.execute(update(Broadcast).where(Broadcast.id == broadcast_id).values(**vals))
        await session.commit()


async def _record(broadcast_id: int, user_id: int, result: SendResult) -> None:
    from sqlalchemy import update

    from app.db.models import BroadcastRecipient, TelegramUser
    from app.db.session import SessionLocal

    vals: dict[str, Any] = {"status": result.status, "sent_at": datetime.utcnow()}
    if result.error is not None:
        vals["error_text"] = result.error[:500]
    async with SessionLocal() as session:
        await session.execute(update(BroadcastRecipient).where(
            BroadcastRecipient.broadcast_id == broadcast_id, BroadcastRecipient.user_telegram_id == user_id).values(**vals))
        if result.status == "blocked":
            await session.execute(update(TelegramUser).where(TelegramUser.telegram_id == user_id).values(is_active=False))
        await session.commit()


async def _credit_sweep(broadcast_id: int, days: int, credit: CreditFn, cancel_flag: asyncio.Event) -> int:
    """Credit every delivered recipient not credited yet (restart safety)."""
    from sqlalchemy import select

    from app.db.models import BroadcastRecipient
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        ids = [r[0] for r in (await session.execute(select(BroadcastRecipient.user_telegram_id).where(
            BroadcastRecipient.broadcast_id == broadcast_id, BroadcastRecipient.status == "sent"))).all()]
    n = 0
    for uid in ids:
        if cancel_flag.is_set():
            break
        if await credit(broadcast_id, uid, days) == "applied":
            n += 1
    return n


async def _run_worker(sender: BroadcastSender, broadcast_id: int, cancel_flag: asyncio.Event,
                      credit: Optional[CreditFn] = None) -> None:
    from sqlalchemy import select

    from app.db.models import Broadcast, BroadcastRecipient
    from app.db.session import SessionLocal

    logger.info(f"broadcast worker start: id={broadcast_id}")
    if not SessionLocal:
        logger.error(f"broadcast worker: no database, abort id={broadcast_id}")
        return
    try:
        async with SessionLocal() as session:
            bc = await session.get(Broadcast, broadcast_id)
            if not bc:
                return
            if bc.started_at is None:
                bc.started_at = datetime.utcnow()
                await session.commit()
            text_html, photo, buttons = bc.text_html, bc.photo_file_id, bc.buttons_json
            silent, days = bc.disable_notification, int(bc.credit_days or 0)
        if days and credit is None:
            logger.error(f"broadcast {broadcast_id}: credit_days={days} but no credit function, not started")
            return
        await materialize_recipients(broadcast_id)
        delivered = failed = blocked = since = 0
        last_send = 0.0
        loop = asyncio.get_running_loop()
        while not cancel_flag.is_set():
            async with SessionLocal() as session:
                chunk = [r[0] for r in (await session.execute(
                    select(BroadcastRecipient.user_telegram_id).where(
                        BroadcastRecipient.broadcast_id == broadcast_id, BroadcastRecipient.status == "pending")
                    .order_by(BroadcastRecipient.id).limit(CHUNK_SIZE))).all()]
            if not chunk:
                break
            for uid in chunk:
                if cancel_flag.is_set():
                    break
                wait = SEND_INTERVAL - (loop.time() - last_send)
                if wait > 0:
                    await asyncio.sleep(wait)
                last_send = loop.time()
                try:
                    res = await sender.send(uid, text_html=text_html, photo_file_id=photo, buttons=buttons,
                                            disable_notification=silent)
                except Exception as e:  # noqa: BLE001 - a sender bug must not stop the broadcast
                    res = SendResult("failed", f"unexpected: {type(e).__name__}")
                await _record(broadcast_id, uid, res)
                delivered += res.status == "sent"
                failed += res.status == "failed"
                blocked += res.status == "blocked"
                if res.status == "sent" and days and credit is not None:
                    await credit(broadcast_id, uid, days)
                since += 1
                if since >= COMMIT_EVERY:
                    await _flush(broadcast_id, delivered, failed, blocked, finished=False)
                    delivered = failed = blocked = since = 0
        if days and credit is not None and not cancel_flag.is_set():
            await _credit_sweep(broadcast_id, days, credit, cancel_flag)
        await _flush(broadcast_id, delivered, failed, blocked, finished=not cancel_flag.is_set())
        logger.info(f"broadcast worker {'cancelled' if cancel_flag.is_set() else 'finished'}: id={broadcast_id}")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"broadcast worker id={broadcast_id} crashed ({type(e).__name__})")
    finally:
        async with _active_workers_lock:
            h = _active_workers.get(broadcast_id)
            if h is not None and h.task is asyncio.current_task():
                _active_workers.pop(broadcast_id, None)


__all__ = [
    "Segment", "SEGMENTS", "VALID_SEGMENTS", "SEGMENT_ALL", "SEGMENT_ACTIVE", "SEGMENT_EXPIRED", "SEGMENT_NEVER",
    "SEGMENT_TRIAL_NC", "SEGMENT_IDS", "segment_filter", "count_segment", "SendResult", "BroadcastSender",
    "credit_one", "credit_code", "BroadcastCredits", "credits_from_container", "start_broadcast",
    "cancel_broadcast", "shutdown_broadcast_worker", "resume_unfinished_broadcasts", "materialize_recipients",
    "BroadcastInfo", "create_draft", "get_broadcast", "list_broadcasts", "delete_draft", "is_running", "set_opt_out",
    "UNSUB_CALLBACK_DATA", "CLOSE_CALLBACK_DATA", "UNSUB_BUTTON_TEXT", "CLOSE_BUTTON_TEXT",
]
