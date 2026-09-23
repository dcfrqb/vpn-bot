"""Дедуп вебхуков YooKassa (payment.* и refund.*), общий для yookassa.py и refunds.py.

Маркер в Redis: yk_event:<event>:<object id>. Значения:
  processing:<trace> — доставка обрабатывается сейчас (TTL 15 мин, чтобы
                       упавший процесс не держал маркер сутки);
  done:<trace>       — событие обработано (TTL 24 ч).

Повторная доставка:
  маркер done        -> 200, ничего не делаем (дубль);
  маркер processing  -> WebhookRetryableError -> 503. YooKassa повторит позже.
                        Раньше тут был 200: если первая доставка потом падала
                        и снимала маркер, ретрай уже был «съеден» (ревью m1/m-4);
  маркера нет        -> обрабатываем.
  SET NX не прошел, а маркера уже нет (первая доставка упала между нашими
  SET и GET) -> еще одна попытка SET NX, иначе 503 (ревью N4). Значения
  маркера всегда непустые, поэтому «нет значения» никогда не значит «done».
Неуспешная обработка (исключение или False) снимает маркер, чтобы повтор
YooKassa после нашего 503 не был проглочен (дефект Д-2). Redis недоступен —
обрабатываем без дедупа, страхует блокировка строки платежа в БД.
"""
import dataclasses
from typing import Any, Awaitable, Callable, Dict, Optional

from app.logger import logger
from app.services.payments.errors import WebhookRetryableError
from app.services.redis_flags import delete_key, get_value, set_once, set_value

WEBHOOK_DEDUP_TTL_SECONDS = 86400
WEBHOOK_PROCESSING_TTL_SECONDS = 900

ACQUIRED = "acquired"
DUPLICATE = "duplicate"
IN_PROGRESS = "in_progress"
UNAVAILABLE = "unavailable"


@dataclasses.dataclass
class WebhookDedup:
    status: str
    key: Optional[str] = None


def webhook_dedup_key(webhook_data: Dict[str, Any], event: str) -> Optional[str]:
    event_id = (webhook_data or {}).get("id") or ((webhook_data or {}).get("object") or {}).get("id")
    if not event_id:
        return None
    return f"yk_event:{event}:{event_id}"


async def acquire_webhook_dedup(webhook_data: Dict[str, Any], event: str, trace_id: str) -> WebhookDedup:
    key = webhook_dedup_key(webhook_data, event)
    if not key:
        return WebhookDedup(UNAVAILABLE)
    got = await set_once(key, f"processing:{trace_id}", ttl=WEBHOOK_PROCESSING_TTL_SECONDS)
    if got is None:
        return WebhookDedup(UNAVAILABLE)
    if got:
        return WebhookDedup(ACQUIRED, key)
    current = await get_value(key)
    if current is None:
        # Ревью N4: между нашим SET NX и GET первая доставка упала и сняла
        # маркер. Раньше пустое значение считалось «done» (200, ретрай съеден).
        # Пробуем взять маркер еще раз; не вышло и значения опять нет -> 503.
        got = await set_once(key, f"processing:{trace_id}", ttl=WEBHOOK_PROCESSING_TTL_SECONDS)
        if got:
            return WebhookDedup(ACQUIRED, key)
        current = await get_value(key)
        if current is None:
            logger.info(f"[{trace_id}] webhook {key}: marker state unknown -> retry later")
            return WebhookDedup(IN_PROGRESS, key)
    if current.startswith("processing"):
        logger.info(f"[{trace_id}] webhook {key}: first delivery still in progress -> retry later")
        return WebhookDedup(IN_PROGRESS, key)
    logger.info(f"[{trace_id}] webhook duplicate suppressed: {key} (already processed)")
    return WebhookDedup(DUPLICATE, key)


async def mark_webhook_done(key: str, trace_id: str) -> None:
    await set_value(key, f"done:{trace_id}", ttl=WEBHOOK_DEDUP_TTL_SECONDS)


async def release_webhook_dedup(key: str, trace_id: str) -> None:
    await delete_key(key)
    logger.info(f"[{trace_id}] webhook dedup released for retry: {key}")


async def run_webhook_once(
    webhook_data: Dict[str, Any],
    event: str,
    trace_id: str,
    handler: Callable[[], Awaitable[bool]],
) -> bool:
    """Выполняет handler() под маркером дедупа. True — обработано (или дубль)."""
    dedup = await acquire_webhook_dedup(webhook_data, event, trace_id)
    if dedup.status == DUPLICATE:
        return True
    if dedup.status == IN_PROGRESS:
        raise WebhookRetryableError(f"webhook {dedup.key} is being processed by another delivery")
    success = False
    try:
        success = bool(await handler())
        return success
    finally:
        if dedup.key and dedup.status == ACQUIRED:
            if success:
                await mark_webhook_done(dedup.key, trace_id)
            else:
                await release_webhook_dedup(dedup.key, trace_id)
