"""YooKassa payment webhook body (release 3.0, stream A).

POST /webhook/yookassa (app.api.routes.yookassa) calls ``process_payment_webhook``.
The webhook is only a trigger: the payment is re-read from the YooKassa API
inside Fulfillment, the body is never trusted. Deduplication is the 2.x
marker ``yk_event:<event>:<id>`` (in progress -> 503, done -> 200, failure
releases it so the 503 retry is processed).

Return value / exceptions (the route maps them):
  True                   processed or nothing to do (200)
  False                  unusable body (200, YooKassa must not retry garbage)
  WebhookRetryableError  API down, grant failed, another delivery in progress (503)
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from app.logger import logger
from app.services.payments.errors import WebhookRetryableError


async def process_payment_webhook(webhook_data: Dict[str, Any], bot: Any = None, *,
                                  container: Any = None) -> bool:
    trace_id = str(uuid.uuid4())
    event = (webhook_data or {}).get("event")
    obj = (webhook_data or {}).get("object") or {}
    external_id: Optional[str] = obj.get("id") if isinstance(obj, dict) else None
    if not event or not external_id:
        logger.error(f"[{trace_id}] yookassa webhook without event/object.id")
        return False

    from app.services.payments.webhook_dedup import run_webhook_once

    async def _body() -> bool:
        return await _process_payment_webhook_body(str(external_id), event, trace_id, container)

    return await run_webhook_once(webhook_data, event, trace_id, _body)


async def _process_payment_webhook_body(external_id: str, event: str, trace_id: str, container: Any) -> bool:
    """Body after dedup: Fulfillment by provider id (re-read from the API)."""
    from app.services.fulfillment import Outcome
    from app.services.money import money

    result = await money(container).fulfillment.process_external(external_id, source="webhook", trace_id=trace_id)
    logger.info(f"[{trace_id}] webhook {event} {external_id}: {result.outcome.value} {result.detail}")
    if result.outcome is Outcome.RETRY:
        raise WebhookRetryableError(f"payment {external_id}: {result.detail or 'retry'}")
    return result.outcome is not Outcome.NOT_FOUND
