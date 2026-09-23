"""Обработка возвратов YooKassa (refund.succeeded), хотфикс 2.1.

Раньше refund.succeeded только логировался: платеж оставался succeeded
(выручка и «всего с клиента» завышены), доступ в Remnawave оставался.

Что делаем:
1. Сверяем возврат через API YooKassa (вебхук только триггер): статус, payment_id,
   сумма; сумму всех возвратов по платежу берем из платежа (refunded_amount).
2. Пишем возврат в payments.payment_metadata["refunds"][<refund_id>]
   (идемпотентно по refund_id) и refunded_amount.
3. Полный возврат (refunded_amount >= amount):
   - payments.status = 'refunded' (выпадает из выручки/статистики, где фильтр
     status='succeeded');
   - оплата тарифа, по которой выдавалась подписка: откатываем ровно
     оплаченный период от текущего expireAt в Remnawave. С 3.0 это делает
     ProvisioningService.rollback под тем же локом, что и выдача. Подписка у юзера одна,
     поэтому возврат старого платежа тоже вычитается из текущей даты.
     Если после отката срок уже в прошлом, юзер НЕ отключается (disable), а
     получает expireAt = сейчас + 5 минут: панель сама переводит его в EXPIRED,
     и следующая оплата, /trial или выдача админом штатно его оживляют
     (фикс-раунд 1, ревью M1: DISABLED ничем не снимался, человек платил
     повторно и оставался без VPN). Подписка и обход гасятся.
     Если срок остался в будущем, для Pro тот же срок ставится и обходу.
     Откат идемпотентен по refund_id (config_data.rollbacks), поэтому повтор
     вебхука не отнимет период дважды и не сотрет выдачу, случившуюся между;
   - пакет обхода или платеж без выданной подписки: доступ не трогаем, только
     алерт (решает админ).
4. Частичный возврат: только запись и алерт админу.
Админу всегда уходит сообщение о возврате (один раз на refund_id).
"""
from datetime import datetime, timezone
from html import escape as _he
from typing import Any, Dict, Optional

from sqlalchemy import select

from app.config import settings
from app.logger import logger
from app.services.payments.errors import WebhookRetryableError

def _rub(value: Any) -> str:
    """129.0 -> "129", 129.5 -> "129.50"."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{v:.0f}" if abs(v - round(v)) < 0.005 else f"{v:.2f}"


def _plan_line(plan_code: Optional[str], period_months: int) -> str:
    if not plan_code:
        return "—"
    from app.core.plans import get_plan_name

    name = get_plan_name(plan_code)
    return f"{name}, {period_months} мес." if period_months else name


async def fetch_refund(refund_id: str) -> Optional[Dict[str, Any]]:
    """Возврат из API YooKassa (3.0: async-шлюз, без SDK) или None при недоступности API."""
    from app.infra.yookassa import default_gateway

    return await default_gateway().get_refund(refund_id)


async def _notify_admins(bot, text: str) -> None:
    for admin_id in (settings.ADMINS or []):
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"refund alert to admin {admin_id} failed: {e}")


def _provisioning():
    """ProvisioningService of the running process (bot or webhook API)."""
    from app.container import get_container

    return get_container().provisioning


async def process_refund_webhook(webhook_data: Dict[str, Any], bot) -> bool:
    """Обработка refund.succeeded. True — обработан; WebhookRetryableError — повторить."""
    from app.db.session import SessionLocal
    from app.db.models import Payment as PaymentModel, Subscription
    from app.services.payments.yookassa import check_payment_status

    obj = (webhook_data or {}).get("object") or {}
    refund_id = obj.get("id")
    if not refund_id:
        logger.error("refund webhook without object.id")
        return False

    refund = await fetch_refund(refund_id)
    if refund is None:
        raise WebhookRetryableError(f"YooKassa API unavailable for refund {refund_id}")
    if refund.get("error") == "not_found":
        logger.warning(f"refund {refund_id}: not found in YooKassa API, ignoring")
        return False
    if refund.get("status") != "succeeded":
        logger.info(f"refund {refund_id}: status={refund.get('status')}, nothing to do")
        return True

    payment_ext_id = refund["payment_id"]
    api_payment = await check_payment_status(payment_ext_id)
    if not api_payment or api_payment.get("error"):
        raise WebhookRetryableError(f"YooKassa API unavailable for payment {payment_ext_id}")
    paid_amount = float(api_payment.get("amount") or 0)
    refunded_total = float(api_payment.get("refunded_amount") or 0) or float(refund["amount"])
    is_full = paid_amount > 0 and refunded_total >= paid_amount - 0.01

    if not SessionLocal:
        raise WebhookRetryableError("DB not configured")

    async with SessionLocal() as session:
        res = await session.execute(
            select(PaymentModel).where(PaymentModel.external_id == payment_ext_id).with_for_update()
        )
        payment = res.scalar_one_or_none()
        if not payment:
            await _notify_admins(bot, (
                "↩️ <b>Возврат по неизвестному платежу</b>\n\n"
                f"Refund: <code>{_he(str(refund_id))}</code>\n"
                f"Payment: <code>{_he(str(payment_ext_id))}</code>\n"
                f"Сумма: {_rub(refund['amount'])} {_he(str(refund.get('currency') or ''))}\n"
                "Платежа нет в БД бота, доступ не трогали."
            ))
            return True

        meta = dict(payment.payment_metadata or {}) if isinstance(payment.payment_metadata, dict) else {}
        refunds = dict(meta.get("refunds") or {})
        entry = dict(refunds.get(refund_id) or {})
        if entry.get("state") == "done":
            logger.info(f"refund {refund_id}: already processed")
            return True

        plan_code = meta.get("plan_code")
        try:
            period_months = int(meta.get("period_months") or 0)
        except (TypeError, ValueError):
            period_months = 0

        from app.core.plans import is_obhod_package_code

        subscription = None
        if payment.subscription_id:
            sres = await session.execute(
                select(Subscription).where(Subscription.id == payment.subscription_id)
            )
            subscription = sres.scalar_one_or_none()

        action = "none"
        action_note = ""
        obhod_note = ""
        # 3.0: возврат по запросу «Не смог подключиться» (refund_requests) уже
        # отключил доступ через ProvisioningService.revoke и написал юзеру.
        by_request = isinstance(meta.get("refund_24h"), dict)
        can_revoke = (
            not by_request
            and is_full
            and subscription is not None
            and getattr(subscription, "sub_kind", "main") == "main"
            and subscription.remna_user_id
            and not is_obhod_package_code(plan_code)
            and period_months > 0
        )

        user_text = None
        new_expire_for_notices: Optional[datetime] = None
        if can_revoke:
            # 3.0 (review architecture R1): the rollback goes through
            # ProvisioningService under the same per-user lock as grants, so a
            # grant or autopay landing at the same time is never erased. The
            # rollback is idempotent per refund id (config_data.rollbacks).
            try:
                result = await _provisioning().rollback(
                    payment.telegram_user_id, months=period_months,
                    reason=f"refund {refund_id}", trace_id=f"refund:{refund_id}",
                )
            except Exception as e:
                raise WebhookRetryableError(f"revoke through provisioning failed: {type(e).__name__}: {e}") from e
            new_dt = result.expire_at
            naive = new_dt.astimezone(timezone.utc).replace(tzinfo=None) if new_dt else None
            if result.action == "expired":
                new_expire_for_notices = new_dt
                action = "expired"
                action_note = (
                    "Срок после отката уже в прошлом: юзеру поставлен expireAt = сейчас "
                    "(панель переведет в EXPIRED), подписка и обход погашены. "
                    "Повторная оплата или выдача снова включит доступ."
                )
                user_text = (
                    "↩️ <b>Возврат оформлен</b>\n\n"
                    "Деньги по платежу возвращены, доступ по этой оплате закончился. "
                    "Если захочешь вернуться, оформи подписку в меню."
                )
            elif result.action == "shortened":
                new_expire_for_notices = new_dt
                action = "shortened"
                action_note = (
                    f"Срок откатан на {period_months} мес. от текущей даты окончания "
                    f"(подписка одна на юзера): до {naive:%d.%m.%Y}." if naive else ""
                )
                user_text = (
                    "↩️ <b>Возврат оформлен</b>\n\n"
                    "Деньги по платежу возвращены, оплаченный период снят. "
                    + (f"Подписка действует до {naive:%d.%m.%Y}." if naive else "")
                )
            else:
                action = result.action
                action_note = (
                    "Доступ НЕ менялся: подписка навсегда или ручной сквад. Решите вручную."
                    if result.action == "skipped" else
                    "Доступ НЕ менялся: аккаунт в панели не найден. Проверьте вручную."
                )
        elif by_request:
            action = "by_request"
            action_note = (
                "Возврат по запросу клиента (24 часа): доступ уже отключен при одобрении"
                if meta["refund_24h"].get("revoked") else
                "Возврат по запросу клиента (24 часа): доступ отключить НЕ удалось, проверь вручную"
            )
        elif is_full:
            action_note = (
                "Доступ НЕ менялся: "
                + ("это пакет обхода." if is_obhod_package_code(plan_code)
                   else "по платежу не выдавалась подписка / неизвестен период.")
                + " Проверьте вручную."
            )
        else:
            action_note = "Частичный возврат: доступ не менялся, решите вручную."

        meta = dict(payment.payment_metadata or {}) if isinstance(payment.payment_metadata, dict) else {}
        refunds = dict(meta.get("refunds") or {})
        entry = dict(refunds.get(refund_id) or {})
        entry.update({
            "state": "done",
            "amount": refund["amount"],
            "currency": refund.get("currency"),
            "full": is_full,
            "action": action,
            "processed_at": datetime.utcnow().isoformat(),
        })
        refunds[refund_id] = entry
        meta["refunds"] = refunds
        meta["refunded_amount"] = refunded_total
        payment.payment_metadata = meta
        if is_full:
            payment.status = "refunded"
            try:
                payment.refunded_amount = refunded_total
            except Exception:  # noqa: BLE001 - column added by r30_01, absent on very old schemas
                pass
            # 3.0: после возврата автопродление не списывает деньги снова.
            if subscription is not None and getattr(subscription, "autorenew", None):
                subscription.autorenew = False
        payment.updated_at = datetime.utcnow()
        await session.commit()

        tg_id = payment.telegram_user_id

    try:
        from app.services.cache import invalidate_subscription_cache, invalidate_sync_cache
        await invalidate_subscription_cache(tg_id)
        await invalidate_sync_cache(tg_id)
    except Exception:
        pass

    # Ревью N1: следом за «Возврат оформлен» не слать «истекает сегодня, продлите».
    if new_expire_for_notices is not None:
        try:
            from app.tasks.expiry_notifier import suppress_expiry_notices
            await suppress_expiry_notices(tg_id, new_expire_for_notices)
        except Exception as e:
            logger.debug(f"refund {refund_id}: suppress expiry notices failed: {e}")

    await _notify_admins(bot, (
        f"↩️ <b>{'Полный' if is_full else 'Частичный'} возврат</b>\n\n"
        f"Telegram ID: <code>{tg_id}</code>\n"
        f"Payment: <code>{_he(str(payment_ext_id))}</code>\n"
        f"Возврат: {_rub(refund['amount'])} ₽ "
        f"(всего возвращено {_rub(refunded_total)} из {_rub(paid_amount)} ₽)\n"
        f"Тариф: {_he(_plan_line(plan_code, period_months))}\n\n"
        f"{_he(action_note + obhod_note)}"
    ))
    if user_text:
        try:
            await bot.send_message(chat_id=tg_id, text=user_text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"refund {refund_id}: user notice to {tg_id} failed: {e}")
    logger.info(
        f"refund processed: refund_id={refund_id} payment={payment_ext_id} tg_id={tg_id} "
        f"full={is_full} action={action}"
    )
    return True


async def handle_refund_webhook(webhook_data: Dict[str, Any], bot) -> bool:
    """refund.succeeded с тем же дедупом, что и платежи (webhook_dedup)."""
    import uuid as _uuid
    from app.services.payments.webhook_dedup import run_webhook_once

    trace_id = str(_uuid.uuid4())
    event = (webhook_data or {}).get("event") or "refund.succeeded"
    return await run_webhook_once(
        webhook_data or {}, event, trace_id,
        lambda: process_refund_webhook(webhook_data, bot),
    )
