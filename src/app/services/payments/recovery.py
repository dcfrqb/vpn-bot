"""Восстановление платежей: needs_provisioning и pending recheck"""
import traceback as _tb
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from sqlalchemy import select
from app.db.session import SessionLocal
from app.db.models import Payment as PaymentModel, TelegramUser
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
    recent_threshold = datetime.utcnow() - timedelta(hours=24)

    async with SessionLocal() as session:
        # Case 1: succeeded without subscription_id (classic missing provisioning)
        stmt_no_sub = select(PaymentModel).where(
            PaymentModel.status == "succeeded",
            PaymentModel.subscription_id.is_(None),
        )
        rows_no_sub = await session.execute(stmt_no_sub)
        payments_no_sub = list(rows_no_sub.scalars().all())

        # Case 2: succeeded with subscription_id set, but Remnawave provisioning failed
        # (subscription was created but get_or_create_remna_user failed; needs_provisioning=True)
        # Limit to last 24h to avoid scanning the full payments table
        stmt_with_sub = select(PaymentModel).where(
            PaymentModel.status == "succeeded",
            PaymentModel.subscription_id.isnot(None),
            PaymentModel.created_at > recent_threshold,
        )
        rows_with_sub = await session.execute(stmt_with_sub)
        payments_with_sub = [
            p for p in rows_with_sub.scalars().all()
            if isinstance(p.payment_metadata, dict) and p.payment_metadata.get("needs_provisioning")
        ]

        # Case 3: succeeded + subscription_id set + remna_user_id still NULL
        # Provisioning silently failed before FK fix (notified=True but Remnawave never updated)
        stmt_case3 = select(PaymentModel).join(
            TelegramUser, TelegramUser.telegram_id == PaymentModel.telegram_user_id
        ).where(
            PaymentModel.status == "succeeded",
            PaymentModel.subscription_id.isnot(None),
            TelegramUser.remna_user_id.is_(None),
        )
        rows_case3 = await session.execute(stmt_case3)
        seen_ids = {p.id for p in payments_no_sub + payments_with_sub}
        payments_case3 = [p for p in rows_case3.scalars().all() if p.id not in seen_ids]

    payments = payments_no_sub + payments_with_sub + payments_case3

    candidates = []
    for payment in payments:
        meta = payment.payment_metadata or {}
        has_needs_provisioning = isinstance(meta, dict) and meta.get("needs_provisioning")
        is_old_enough = payment.created_at < fallback_threshold if payment.created_at else False
        is_unprovisioned = payment in payments_case3
        if has_needs_provisioning or is_old_enough or is_unprovisioned:
            candidates.append(payment)

    if candidates:
        logger.info(
            f"recovery_provision_scan: found={len(candidates)} succeeded_without_subscription "
            f"(total_checked={len(payments)})"
        )

    from app.services.cache import acquire_provision_lock, release_provision_lock

    for payment in candidates:
        result["processed"] += 1
        logger.info(
            f"recovery_provision_retry: payment_id={payment.id} "
            f"tg_id={payment.telegram_user_id} amount={payment.amount}"
        )
        external_id = payment.external_id or str(payment.id)
        lock_acquired = await acquire_provision_lock(external_id)
        if not lock_acquired:
            logger.info(
                f"recovery_provision_retry: provision_lock busy external_id={external_id} "
                f"— skipping this cycle, will retry next run"
            )
            result["processed"] -= 1
            continue
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
                    logger.info(
                        f"recovery_provision_success: payment_id={payment.id} "
                        f"subscription_id={p.subscription_id}"
                    )
                else:
                    logger.warning(
                        f"recovery_provision_skipped: payment_id={payment.id} "
                        f"subscription_id still None after handle_successful_payment"
                    )
        except Exception as e:
            result["errors"] += 1
            logger.error(
                f"recovery_provision_failed: payment_id={payment.id} "
                f"tg_id={payment.telegram_user_id} err={e}"
            )
            logger.debug(_tb.format_exc())
        finally:
            await release_provision_lock(external_id)

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

    logger.info(f"[{trace_id}] recheck_single: checking YooKassa external_id={external_id} tg_id={payment.telegram_user_id}")
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
                finally:
                    await release_provision_lock(external_id)
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

    if payments:
        logger.info(f"recovery_pending_scan: found={len(payments)} pending payments older than {PENDING_RECHECK_MINUTES}min")

    for payment in payments:
        result["checked"] += 1
        try:
            status_data = await check_payment_status(payment.external_id)
            if not status_data or status_data.get("status") == "pending":
                continue
            if status_data.get("error") == "not_found":
                logger.warning(
                    f"recovery_pending_notfound: payment_id={payment.id} "
                    f"external_id={payment.external_id} not found in YooKassa"
                )
                continue
            new_status = status_data["status"]
            amount = float(status_data.get("amount", payment.amount))
            logger.info(
                f"recovery_pending_update: payment_id={payment.id} "
                f"tg_id={payment.telegram_user_id} pending->{new_status} amount={amount}"
            )
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
                        logger.info(f"recovery_pending_provisioned: payment_id={payment.id}")
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
            logger.error(
                f"recovery_pending_error: payment_id={payment.id} "
                f"external_id={payment.external_id} err={e}"
            )
            logger.debug(_tb.format_exc())

    return result
