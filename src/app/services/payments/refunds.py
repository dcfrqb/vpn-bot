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
     оплаченный период от текущего expireAt в Remnawave. Если после отката
     срок уже в прошлом — юзер отключается (disable), подписка и обход гасятся.
     Цель отката вычисляется один раз и сохраняется (абсолютная дата), поэтому
     повтор вебхука не отнимет период дважды;
   - пакет обхода или платеж без выданной подписки: доступ не трогаем, только
     алерт (решает админ).
4. Частичный возврат: только запись и алерт админу.
Админу всегда уходит сообщение о возврате (один раз на refund_id).
"""
import asyncio
from datetime import datetime, timezone
from html import escape as _he
from typing import Any, Dict, Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from app.config import settings
from app.logger import logger
from app.services.payments.errors import WebhookRetryableError

REFUND_DISABLE = "disable"


async def fetch_refund(refund_id: str) -> Optional[Dict[str, Any]]:
    """Возврат из API YooKassa или None при недоступности API."""
    try:
        from yookassa import Refund

        refund = await asyncio.to_thread(Refund.find_one, refund_id)
    except Exception as e:
        logger.error(f"refund {refund_id}: YooKassa API error: {e}")
        return None
    if not refund:
        return {"error": "not_found"}
    try:
        return {
            "id": refund.id,
            "status": refund.status,
            "payment_id": refund.payment_id,
            "amount": float(refund.amount.value),
            "currency": refund.amount.currency,
        }
    except Exception as e:
        logger.error(f"refund {refund_id}: unexpected API response: {e}")
        return None


def _parse_expire(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _notify_admins(bot, text: str) -> None:
    for admin_id in (settings.ADMINS or []):
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"refund alert to admin {admin_id} failed: {e}")


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
                f"Сумма: {refund['amount']} {_he(str(refund.get('currency') or ''))}\n"
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
        can_revoke = (
            is_full
            and subscription is not None
            and getattr(subscription, "sub_kind", "main") == "main"
            and subscription.remna_user_id
            and not is_obhod_package_code(plan_code)
            and period_months > 0
        )

        from app.remnawave.client import RemnaClient, normalize_expire_at

        if can_revoke:
            client = RemnaClient()
            try:
                target = entry.get("target_expire")
                if not target:
                    # Фиксируем цель один раз (абсолютная дата), чтобы повтор не
                    # отнял период дважды.
                    try:
                        data = await client.get_user_by_id(str(subscription.remna_user_id))
                    except Exception as e:
                        raise WebhookRetryableError(f"remnawave read failed: {e}") from e
                    raw = data.get("response", data) if isinstance(data, dict) else {}
                    current = _parse_expire((raw or {}).get("expireAt"))
                    now = datetime.now(timezone.utc)
                    if current is None:
                        target = REFUND_DISABLE
                    else:
                        new_expire = current - relativedelta(months=period_months)
                        target = REFUND_DISABLE if new_expire <= now else normalize_expire_at(new_expire)
                    entry.update({"state": "pending", "target_expire": target})
                    refunds[refund_id] = entry
                    meta["refunds"] = refunds
                    payment.payment_metadata = dict(meta)
                    await session.commit()
                    # после commit лок снят — берем заново
                    res = await session.execute(
                        select(PaymentModel).where(PaymentModel.id == payment.id).with_for_update()
                    )
                    payment = res.scalar_one_or_none()

                try:
                    if target == REFUND_DISABLE:
                        await client.disable_user(str(subscription.remna_user_id))
                        subscription.active = False
                        subscription.provisioning_state = "expired"
                        subscription.last_provisioning_error = f"refund {refund_id}"
                        try:
                            from app.services.obhod_service import deactivate_obhod
                            await deactivate_obhod(session, payment.telegram_user_id, trace_id=f"refund_{refund_id}")
                        except Exception as e:
                            logger.warning(f"refund {refund_id}: obhod deactivate soft-fail: {e}")
                        action = "disabled"
                        action_note = "Юзер отключен в Remnawave (срок после отката уже в прошлом), подписка погашена."
                    else:
                        await client.update_user(str(subscription.remna_user_id), expire_at=target)
                        new_dt = _parse_expire(target)
                        naive = new_dt.replace(tzinfo=None) if new_dt else None
                        subscription.valid_until = naive
                        subscription.remnawave_expected_expire_at = naive
                        action = "shortened"
                        action_note = f"Срок откатан на {period_months} мес.: до {naive:%d.%m.%Y}." if naive else ""
                except Exception as e:
                    raise WebhookRetryableError(f"remnawave revoke failed: {e}") from e
            finally:
                try:
                    await client.close()
                except Exception:
                    pass
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
        payment.updated_at = datetime.utcnow()
        await session.commit()

        tg_id = payment.telegram_user_id

    try:
        from app.services.cache import invalidate_subscription_cache, invalidate_sync_cache
        await invalidate_subscription_cache(tg_id)
        await invalidate_sync_cache(tg_id)
    except Exception:
        pass

    await _notify_admins(bot, (
        f"↩️ <b>{'Полный' if is_full else 'Частичный'} возврат</b>\n\n"
        f"Telegram ID: <code>{tg_id}</code>\n"
        f"Payment: <code>{_he(str(payment_ext_id))}</code>\n"
        f"Возврат: {refund['amount']} {_he(str(refund.get('currency') or ''))} "
        f"(всего возвращено {refunded_total} из {paid_amount})\n"
        f"Тариф: {_he(str(plan_code))} {period_months or ''} мес.\n\n"
        f"{_he(action_note)}"
    ))
    logger.info(
        f"refund processed: refund_id={refund_id} payment={payment_ext_id} tg_id={tg_id} "
        f"full={is_full} action={action}"
    )
    return True


async def handle_refund_webhook(webhook_data: Dict[str, Any], bot) -> bool:
    """refund.succeeded с тем же дедупом, что и платежи (маркер снимается при неудаче)."""
    import uuid as _uuid
    from app.services.payments.yookassa import _acquire_webhook_dedup, _release_webhook_dedup

    trace_id = str(_uuid.uuid4())
    event = (webhook_data or {}).get("event") or "refund.succeeded"
    dedup_key = await _acquire_webhook_dedup(webhook_data or {}, event, trace_id)
    if dedup_key is False:
        return True
    success = False
    try:
        success = await process_refund_webhook(webhook_data, bot)
        return success
    finally:
        if dedup_key and not success:
            await _release_webhook_dedup(dedup_key, trace_id)
