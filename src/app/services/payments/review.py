"""Решение админа по платежу на ручной проверке (фикс-раунд 1, ревью m5).

Раньше снять платеж с ревью можно было только правкой
payment_metadata.review_approved=true в SQL на проде. Теперь под алертом
админу две кнопки:
  «Одобрить и выдать» — review_approved=true и сразу обычная выдача через
      handle_successful_payment (та же функция, что у вебхука: подписка,
      обход, уведомления юзеру и админам). Если панель не ответила, одобрение
      остается, recovery доведет выдачу сам;
  «Отклонить»       — review_rejected=true, доступ не выдается, recovery
      платеж не трогает, деньги возвращает админ в кабинете YooKassa.
Идемпотентно: лок на платеж, повторное нажатие после выдачи ничего не делает,
одобрить отклоненный (и наоборот) нельзя.
"""
import uuid
from datetime import datetime
from typing import Tuple

from sqlalchemy import select

from app.logger import logger

APPROVED = "approved"
PENDING = "pending"          # одобрено, но выдача не прошла: recovery повторит
REJECTED = "rejected"
ALREADY_DONE = "already_done"
ALREADY_REJECTED = "already_rejected"
ALREADY_APPROVED = "already_approved"
NOT_HELD = "not_held"
NOT_PAID = "not_paid"
NOT_FOUND = "not_found"
BUSY = "busy"

RESULT_TEXT = {
    APPROVED: "✅ Одобрено, доступ выдан.",
    PENDING: "⏳ Одобрено, но выдача не прошла (панель). Бот повторит сам (recovery).",
    REJECTED: "❌ Отклонено. Доступ не выдан, оформите возврат в кабинете YooKassa.",
    ALREADY_DONE: "ℹ️ Уже одобрено и выдано.",
    ALREADY_REJECTED: "ℹ️ Платеж уже отклонен.",
    ALREADY_APPROVED: "ℹ️ Платеж уже одобрен, отклонить нельзя.",
    NOT_HELD: "ℹ️ Платеж не на ручной проверке.",
    NOT_PAID: "⚠️ Платеж не в статусе succeeded, выдавать нечего.",
    NOT_FOUND: "⚠️ Платеж не найден.",
    BUSY: "⏳ Решение по этому платежу уже обрабатывается.",
}


def _already_provisioned(payment, meta: dict) -> bool:
    return bool(payment.subscription_id) or meta.get("obhod_package_applied") is True


async def decide_held_payment(payment_row_id: int, admin_id: int, approve: bool, bot) -> Tuple[str, str]:
    """Возвращает (код результата, текст для админа)."""
    from app.db.models import Payment as PaymentModel
    from app.db.session import SessionLocal
    from app.services.user_lock import user_action_lock

    trace_id = f"review_{uuid.uuid4().hex[:8]}"
    async with user_action_lock("payment_review", int(payment_row_id)) as acquired:
        if not acquired:
            return BUSY, RESULT_TEXT[BUSY]
        async with SessionLocal() as session:
            res = await session.execute(
                select(PaymentModel).where(PaymentModel.id == int(payment_row_id)).with_for_update()
            )
            payment = res.scalar_one_or_none()
            if not payment:
                return NOT_FOUND, RESULT_TEXT[NOT_FOUND]
            meta = dict(payment.payment_metadata or {}) if isinstance(payment.payment_metadata, dict) else {}
            if not meta.get("needs_review"):
                return NOT_HELD, RESULT_TEXT[NOT_HELD]
            if meta.get("review_rejected"):
                return ALREADY_REJECTED, RESULT_TEXT[ALREADY_REJECTED]

            if not approve:
                if meta.get("review_approved"):
                    return ALREADY_APPROVED, RESULT_TEXT[ALREADY_APPROVED]
                meta.update({
                    "review_rejected": True,
                    "review_decided_by": int(admin_id),
                    "review_decided_at": datetime.utcnow().isoformat(),
                })
                payment.payment_metadata = meta
                await session.commit()
                logger.warning(
                    f"[{trace_id}] payment review rejected: payment_id={payment.id} "
                    f"external_id={payment.external_id} by admin={admin_id}"
                )
                return REJECTED, RESULT_TEXT[REJECTED]

            if meta.get("review_approved") and _already_provisioned(payment, meta):
                return ALREADY_DONE, RESULT_TEXT[ALREADY_DONE]
            if payment.status != "succeeded":
                return NOT_PAID, RESULT_TEXT[NOT_PAID]

            # Ревью N3: тот же лок, что у recovery и вебхука
            # (provision_lock:<external_id>). Иначе одобрение во время цикла
            # recovery давало две выдачи параллельно и два «оплата подтверждена».
            from app.services.cache import acquire_provision_lock, release_provision_lock

            if not await acquire_provision_lock(payment.external_id):
                return BUSY, RESULT_TEXT[BUSY]
            try:
                return await _approve_and_provision(session, payment, meta, admin_id, bot, trace_id)
            finally:
                await release_provision_lock(payment.external_id)


async def _approve_and_provision(session, payment, meta: dict, admin_id: int, bot, trace_id: str) -> Tuple[str, str]:
    """Одобрение и выдача под provision_lock (см. decide_held_payment)."""
    from app.services.payments.errors import ProvisioningError

    meta.update({
        "review_approved": True,
        "review_decided_by": int(admin_id),
        "review_decided_at": datetime.utcnow().isoformat(),
    })
    payment.payment_metadata = meta
    await session.commit()
    logger.warning(
        f"[{trace_id}] payment review approved: payment_id={payment.id} "
        f"external_id={payment.external_id} by admin={admin_id}"
    )

    from app.services.payments.yookassa import handle_successful_payment
    try:
        await handle_successful_payment(
            session=session,
            payment_id=payment.id,
            telegram_user_id=int(payment.telegram_user_id),
            amount=float(payment.amount),
            description=payment.description or "CRS VPN",
            bot=bot,
            trace_id=trace_id,
        )
    except ProvisioningError as e:
        logger.error(f"[{trace_id}] approved payment provisioning pending: {e}")
        return PENDING, RESULT_TEXT[PENDING]
    return APPROVED, RESULT_TEXT[APPROVED]
