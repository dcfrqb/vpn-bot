"""Восстановление платежей (3.0: все делает app.services.fulfillment).

Имена 2.x оставлены для SubscriptionChecker и legacy/routers/payments.py
(его кнопки в 3.0 перехватывает новый роутер через legacy_aliases).
"""
from typing import Dict, Any, Optional

from app.logger import logger


PENDING_RECHECK_MINUTES = 15
PROVISIONING_FALLBACK_MINUTES = 30  # Минимальный возраст для retry без needs_provisioning


async def retry_needs_provisioning(bot) -> Dict[str, Any]:
    """3.0: один проход recovery через Fulfillment (app.services.fulfillment.recover):
    pending старше 15 минут сверяются с YooKassa, оплаченные без выдачи выдаются
    повторно, платежи на ревью не трогаются, повторная выдача идемпотентна.

    Зовется из SubscriptionChecker (2.x, флаг TASK_RECOVERY_ENABLED); тот же проход
    делает задача планировщика app.worker.jobs.recovery (поток A).
    """
    try:
        from app.services.money import money

        return await money().fulfillment.recover()
    except RuntimeError as e:  # container not built (tests, scripts)
        logger.warning(f"recovery skipped: {e}")
        return {"checked": 0}


async def recheck_single_payment(
    external_id: str,
    bot,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """2.x «Проверить оплату» (legacy router). 3.0: Fulfillment.process_external.

    Result dict as in 2.x: updated/status/provisioned/error."""
    from app.services.fulfillment import Outcome
    from app.services.money import money

    result = await money().fulfillment.process_external(str(external_id), source="check", trace_id=trace_id)
    rec = result.payment
    o = result.outcome
    status = {
        Outcome.FULFILLED: "succeeded", Outcome.ALREADY: "succeeded", Outcome.PENDING: "pending",
        Outcome.CANCELED: "canceled", Outcome.REFUNDED: "refunded", Outcome.HELD: "review",
        Outcome.REJECTED: "review_rejected",
    }.get(o, rec.status if rec else None)
    error = {Outcome.NOT_FOUND: "not_found", Outcome.RETRY: "provisioning_pending" if rec and rec.status == "succeeded"
             else "api_error"}.get(o)
    return {"updated": o in (Outcome.FULFILLED, Outcome.CANCELED), "status": status,
            "provisioned": o in (Outcome.FULFILLED, Outcome.ALREADY), "error": error}


async def recheck_pending_payments(bot) -> Dict[str, Any]:
    """3.0: pending-платежи проверяет тот же проход, что и retry_needs_provisioning
    (Fulfillment.recover). Оставлено пустым, чтобы SubscriptionChecker не делал
    проход дважды за цикл."""
    return {"checked": 0, "updated": 0, "errors": 0, "delegated": "retry_needs_provisioning"}
