"""POST /webhook/remnawave - Remnawave panel webhooks. Owner stream: C.

Signature scheme (verified against remnawave/backend 3.4.3,
src/queue/notifications/webhook-logger/webhook-logger.processor.ts):
  X-Remnawave-Signature = hex(HMAC-SHA256(WEBHOOK_SECRET_HEADER, raw body))
  X-Remnawave-Timestamp = ISO time of the event (NOT covered by the HMAC)
The body itself carries the same ``timestamp`` field, and the body IS signed,
so the replay window is checked on the body timestamp.

Pipeline, all before any work:
  1. PANEL_WEBHOOK_SECRET empty -> 503 (feature off; the panel retries 3x
     and gives up, nothing is processed);
  2. bad or missing signature -> 401;
  3. body not JSON / not an event -> 400;
  4. body timestamp older than MAX_AGE_S or more than MAX_FUTURE_S ahead ->
     200 {"status": "stale"} (acknowledged, ignored: a 4xx would only make
     the panel retry the same stale body);
  5. dedupe on sha256(body) in Redis (``rw_webhook:<sha>``): a repeat -> 200
     {"status": "duplicate"}; Redis down -> processed anyway (the handlers
     are idempotent through Notifier dedup keys and conditional DB marks);
  6. 200 {"status": "accepted"} at once; the event is processed in a
     background task (app.worker.panel_events).
The body is never logged (it holds user credentials).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from app.logger import logger

router = APIRouter()

SIGNATURE_HEADER = "X-Remnawave-Signature"
TIMESTAMP_HEADER = "X-Remnawave-Timestamp"
MAX_AGE_S = 30 * 60  # panel queue retries 3x5 s; leave room for a short outage
MAX_FUTURE_S = 5 * 60
DEDUP_PREFIX = "rw_webhook:"
DEDUP_PROCESSING_TTL = 10 * 60
DEDUP_DONE_TTL = 2 * 3600  # > MAX_AGE_S: a replay inside the window is always seen
MAX_BODY = 1024 * 1024


def _secret() -> Optional[str]:
    from app.config import settings

    value = (settings.PANEL_WEBHOOK_SECRET or "").strip()
    return value or None


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def signature_ok(body: bytes, header: Optional[str], secret: str) -> bool:
    if not header:
        return False
    return hmac.compare_digest(sign(body, secret), header.strip().lower())


def within_window(ts: Optional[datetime], now: datetime) -> bool:
    if ts is None:
        return False
    return now - timedelta(seconds=MAX_AGE_S) <= ts <= now + timedelta(seconds=MAX_FUTURE_S)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _default_processor(container: Any):
    from app.worker.panel_events import PanelEventProcessor

    return PanelEventProcessor(container)


# Tests replace these two.
clock: Callable[[], datetime] = _now
processor_factory: Callable[[Any], Any] = _default_processor


async def process_in_background(event: Any, dedup_key: Optional[str], trace_id: str) -> None:
    from app.container import get_container
    from app.domain.models import AdminTopic
    from app.domain.texts import notify as T
    from app.infra.redis.flags import mark_marker_done

    container = None
    try:
        container = get_container()
        await processor_factory(container).process(event)
    except Exception as e:  # noqa: BLE001 - the panel already got 200
        logger.exception(f"[{trace_id}] remnawave webhook {event.event} failed: {type(e).__name__}")
        if container is not None:
            try:
                await container.notifier.notify_admins(
                    AdminTopic.ERRORS, T.admin_webhook_error(event.event, type(e).__name__),
                    dedup_key=f"rw_err:{event.event}:{type(e).__name__}", dedup_ttl=3600,
                )
            except Exception:  # noqa: BLE001
                pass
    finally:
        if dedup_key:
            await mark_marker_done(dedup_key, trace_id, ttl=DEDUP_DONE_TTL)


@router.post("/webhook/remnawave")
async def remnawave_webhook(request: Request, background: BackgroundTasks) -> JSONResponse:
    from app.infra.redis.flags import MARKER_DUPLICATE, MARKER_IN_PROGRESS, MARKER_UNAVAILABLE, acquire_marker
    from app.worker.panel_events import parse_event

    secret = _secret()
    if secret is None:
        return JSONResponse(status_code=503, content={"status": "disabled"})

    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse(status_code=413, content={"status": "too_large"})
    if not signature_ok(body, request.headers.get(SIGNATURE_HEADER), secret):
        logger.warning("remnawave webhook: bad signature")
        return JSONResponse(status_code=401, content={"status": "bad_signature"})

    try:
        event = parse_event(json.loads(body))
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"status": "bad_payload"})

    if not within_window(event.timestamp, clock()):
        logger.warning(f"remnawave webhook: {event.event} outside the time window, ignored")
        return JSONResponse(status_code=200, content={"status": "stale"})

    trace_id = f"rw-{uuid.uuid4().hex[:8]}"
    key = DEDUP_PREFIX + hashlib.sha256(body).hexdigest()
    marker = await acquire_marker(key, trace_id, DEDUP_PROCESSING_TTL)
    if marker in (MARKER_DUPLICATE, MARKER_IN_PROGRESS):
        return JSONResponse(status_code=200, content={"status": "duplicate"})
    if marker == MARKER_UNAVAILABLE:
        logger.warning(f"[{trace_id}] remnawave webhook: Redis down, processing without dedupe")
        key = None

    background.add_task(process_in_background, event, key, trace_id)
    return JSONResponse(status_code=200, content={"status": "accepted"})
