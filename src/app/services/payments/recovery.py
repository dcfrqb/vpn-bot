"""Восстановление платежей (3.0: проход делает app.services.fulfillment).

recheck_single_payment остался для 2.x-роутера legacy/routers/payments.py
(в 3.0 его кнопки перехватывает новый роутер через legacy_aliases).
"""
from datetime import datetime
from typing import Dict, Any, Optional

from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import Payment as PaymentModel
from app.logger import logger
from app.services.payments.errors import ProvisioningPendingError


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
    """
    Точечная проверка одного платежа по external_id.
    Используется кнопкой "Проверить оплату" для быстрой синхронизации статуса.
    Provisioning вызывается только если payment_db.status == succeeded (после обновления).
    """
    import uuid as _uuid
    trace_id = trace_id or str(_uuid.uuid4())
    result = {"updated": False, "status": None, "provisioned": False, "error": None}

    if not SessionLocal:
        result["error"] = "db_unavailable"
        return result

    try:
        from app.services.payments.yookassa import check_payment_status, handle_successful_payment
    except ImportError:
        result["error"] = "import_error"
        return result

    async with SessionLocal() as session:
        stmt = select(PaymentModel).where(
            PaymentModel.external_id == external_id,
            PaymentModel.provider == "yookassa",
        )
        pay_result = await session.execute(stmt)
        payment = pay_result.scalar_one_or_none()

    if not payment:
        logger.warning(f"[{trace_id}] recheck_single: payment not found external_id={external_id}")
        result["error"] = "not_found"
        return result

    _pmeta = payment.payment_metadata if isinstance(payment.payment_metadata, dict) else {}
    if _pmeta.get("needs_review") and not _pmeta.get("review_approved"):
        result["status"] = "review_rejected" if _pmeta.get("review_rejected") else "review"
        return result

    if payment.status == "succeeded" and payment.subscription_id:
        logger.info(f"[{trace_id}] recheck_single: already done external_id={external_id} tg_user={payment.telegram_user_id}")
        result["status"] = "succeeded"
        result["provisioned"] = True
        return result

    if payment.status != "pending":
        logger.info(f"[{trace_id}] recheck_single: not pending external_id={external_id} status={payment.status}")
        result["status"] = payment.status
        return result

    logger.info(f"[{trace_id}] recheck_single: checking YooKassa external_id={external_id} tg_id={payment.telegram_user_id}")
    status_data = await check_payment_status(external_id)
    if not status_data:
        result["error"] = "api_error"
        return result

    # YooKassa вернула "платеж не найден" — не меняем статус в БД, только информируем
    if status_data.get("error") == "not_found":
        logger.warning(
            f"[{trace_id}] recheck_single: YooKassa not found external_id={external_id} "
            f"tg_user_id={payment.telegram_user_id}"
        )
        result["error"] = "not_found"
        return result

    new_status = status_data.get("status")
    if new_status == "pending":
        result["status"] = "pending"
        return result

    amount = float(status_data.get("amount", payment.amount))
    logger.info(
        f"[{trace_id}] recheck_single: status_changed external_id={external_id} "
        f"pending->{new_status} amount={amount}"
    )

    async with SessionLocal() as session:
        pay_result = await session.execute(
            select(PaymentModel).where(PaymentModel.id == payment.id)
        )
        p = pay_result.scalar_one_or_none()
        if not p:
            result["error"] = "not_found"
            return result

        if p.status != "pending":
            result["status"] = p.status
            result["provisioned"] = bool(p.subscription_id)
            return result

        p.status = new_status
        if new_status == "succeeded":
            p.paid_at = datetime.utcnow()
            p.amount = amount

        if new_status == "succeeded" and not p.subscription_id:
            # Commit status update before provisioning: ensures status is persisted
            # even if provisioning fails partway through.
            await session.commit()
            from app.services.cache import acquire_provision_lock, release_provision_lock
            lock_acquired = await acquire_provision_lock(external_id)
            if not lock_acquired:
                logger.info(
                    f"[{trace_id}] recheck_single: provision_lock busy external_id={external_id} "
                    f"— another process is provisioning"
                )
                result["provisioned"] = False
            else:
                try:
                    outcome = await handle_successful_payment(
                        session=session,
                        payment_id=p.id,
                        telegram_user_id=p.telegram_user_id,
                        amount=amount,
                        description=p.description or "CRS VPN",
                        bot=bot,
                        trace_id=trace_id,
                    )
                    if outcome == "review":
                        result["provisioned"] = False
                        result["updated"] = True
                        result["status"] = "review"
                        return result
                    result["provisioned"] = True
                    logger.info(f"[{trace_id}] recheck_single: provisioned external_id={external_id} tg_user={p.telegram_user_id}")
                except ProvisioningPendingError as ppe:
                    logger.warning(
                        f"[{trace_id}] recheck_single: provisioning pending external_id={external_id} "
                        f"reason={str(ppe)[:200]}"
                    )
                    result["provisioned"] = False
                    result["error"] = "provisioning_pending"
                finally:
                    await release_provision_lock(external_id)
        else:
            await session.commit()

        result["updated"] = True
        result["status"] = new_status

    return result


async def recheck_pending_payments(bot) -> Dict[str, Any]:
    """3.0: pending-платежи проверяет тот же проход, что и retry_needs_provisioning
    (Fulfillment.recover). Оставлено пустым, чтобы SubscriptionChecker не делал
    проход дважды за цикл."""
    return {"checked": 0, "updated": 0, "errors": 0, "delegated": "retry_needs_provisioning"}
