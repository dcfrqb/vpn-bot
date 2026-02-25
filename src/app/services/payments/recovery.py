"""Восстановление платежей: needs_provisioning и pending recheck"""
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import Payment as PaymentModel
from app.logger import logger


PENDING_RECHECK_MINUTES = 15
PROVISIONING_FALLBACK_MINUTES = 30  # Минимальный возраст для retry без needs_provisioning


async def retry_needs_provisioning(bot) -> Dict[str, Any]:
    """
    Повторяет provisioning для платежей с needs_provisioning=True.
    Также обрабатывает succeeded без подписки старше PROVISIONING_FALLBACK_MINUTES
    (на случай, если needs_provisioning не был установлен из-за сбоя).
    Вызывается периодически из SubscriptionChecker.
    """
    result = {"processed": 0, "succeeded": 0, "errors": 0}
    if not SessionLocal:
        return result

    try:
        from app.services.payments.yookassa import handle_successful_payment
    except ImportError:
        logger.warning("handle_successful_payment not available for recovery")
        return result

    fallback_threshold = datetime.utcnow() - timedelta(minutes=PROVISIONING_FALLBACK_MINUTES)

    async with SessionLocal() as session:
        stmt = select(PaymentModel).where(
            PaymentModel.status == "succeeded",
            PaymentModel.subscription_id.is_(None),
        )
        rows = await session.execute(stmt)
        payments = list(rows.scalars().all())

    for payment in payments:
        meta = payment.payment_metadata or {}
        has_needs_provisioning = isinstance(meta, dict) and meta.get("needs_provisioning")
        # Fallback: succeeded без подписки и без флага — если платёж старше N минут
        is_old_enough = payment.created_at < fallback_threshold if payment.created_at else False
        if not has_needs_provisioning and not is_old_enough:
            continue

        result["processed"] += 1
        try:
            async with SessionLocal() as session:
                await handle_successful_payment(
                    session=session,
                    payment_id=payment.id,
                    telegram_user_id=payment.telegram_user_id,
                    amount=float(payment.amount),
                    description=payment.description or "CRS VPN",
                    bot=bot,
                )
            async with SessionLocal() as session:
                pay_result = await session.execute(
                    select(PaymentModel).where(PaymentModel.id == payment.id)
                )
                p = pay_result.scalar_one_or_none()
                if p and p.subscription_id:
                    meta = p.payment_metadata or {}
                    if isinstance(meta, dict) and meta.get("needs_provisioning"):
                        meta = dict(meta)
                        meta.pop("needs_provisioning", None)
                        meta.pop("provisioning_attempted_at", None)
                        meta.pop("provisioning_error", None)
                        p.payment_metadata = meta
                        await session.commit()
                    result["succeeded"] += 1
                    logger.info(f"recovery: payment_id={payment.id} provisioning succeeded")
        except Exception as e:
            result["errors"] += 1
            logger.error(f"recovery: payment_id={payment.id} provisioning failed: {e}")

    return result


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

    if payment.status == "succeeded" and payment.subscription_id:
        logger.info(f"[{trace_id}] recheck_single: already done external_id={external_id} tg_user={payment.telegram_user_id}")
        result["status"] = "succeeded"
        result["provisioned"] = True
        return result

    if payment.status != "pending":
        logger.info(f"[{trace_id}] recheck_single: not pending external_id={external_id} status={payment.status}")
        result["status"] = payment.status
        return result

    status_data = await check_payment_status(external_id)
    if not status_data:
        result["error"] = "api_error"
        return result

    # YooKassa вернула "платёж не найден" — не меняем статус в БД, только информируем
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
            await handle_successful_payment(
                session=session,
                payment_id=p.id,
                telegram_user_id=p.telegram_user_id,
                amount=amount,
                description=p.description or "CRS VPN",
                bot=bot,
                trace_id=trace_id,
            )
            result["provisioned"] = True
            logger.info(f"[{trace_id}] recheck_single: provisioned external_id={external_id} tg_user={p.telegram_user_id}")
        else:
            await session.commit()

        result["updated"] = True
        result["status"] = new_status

    return result


async def recheck_pending_payments(bot) -> Dict[str, Any]:
    """
    Проверяет статус pending-платежей старше N минут через API провайдера.
    """
    result = {"checked": 0, "updated": 0, "errors": 0}
    if not SessionLocal:
        return result

    try:
        from app.services.payments.yookassa import check_payment_status, handle_successful_payment
    except ImportError:
        return result

    threshold = datetime.utcnow() - timedelta(minutes=PENDING_RECHECK_MINUTES)

    async with SessionLocal() as session:
        stmt = select(PaymentModel).where(
            PaymentModel.status == "pending",
            PaymentModel.provider == "yookassa",
            PaymentModel.created_at < threshold,
        ).limit(20)
        rows = await session.execute(stmt)
        payments = list(rows.scalars().all())

    for payment in payments:
        result["checked"] += 1
        try:
            status_data = await check_payment_status(payment.external_id)
            if not status_data or status_data.get("status") == "pending":
                continue
            new_status = status_data["status"]
            amount = float(status_data.get("amount", payment.amount))
            if new_status == "succeeded":
                async with SessionLocal() as session:
                    pay_result = await session.execute(
                        select(PaymentModel).where(PaymentModel.id == payment.id)
                    )
                    p = pay_result.scalar_one_or_none()
                    if p and p.status == "pending" and not p.subscription_id:
                        p.status = "succeeded"
                        p.paid_at = datetime.utcnow()
                        p.amount = amount
                        await session.commit()
                        await handle_successful_payment(
                            session=session,
                            payment_id=p.id,
                            telegram_user_id=p.telegram_user_id,
                            amount=amount,
                            description=p.description or "CRS VPN",
                            bot=bot,
                        )
                        result["updated"] += 1
                        logger.info(f"recheck: payment_id={payment.id} updated to succeeded")
            else:
                async with SessionLocal() as session:
                    pay_result = await session.execute(
                        select(PaymentModel).where(PaymentModel.id == payment.id)
                    )
                    p = pay_result.scalar_one_or_none()
                    if p and p.status == "pending":
                        p.status = new_status
                        await session.commit()
                        result["updated"] += 1
        except Exception as e:
            result["errors"] += 1
            logger.debug(f"recheck pending payment_id={payment.id}: {e}")

    return result
