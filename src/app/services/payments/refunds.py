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
     оплаченный период от текущего expireAt в Remnawave. Подписка у юзера одна,
     поэтому возврат старого платежа тоже вычитается из текущей даты.
     Если после отката срок уже в прошлом, юзер НЕ отключается (disable), а
     получает expireAt = сейчас + 5 минут: панель сама переводит его в EXPIRED,
     и следующая оплата, /trial или выдача админом штатно его оживляют
     (фикс-раунд 1, ревью M1: DISABLED ничем не снимался, человек платил
     повторно и оставался без VPN). Подписка и обход гасятся.
     Если срок остался в будущем, для Pro тот же срок ставится и обходу.
     Цель отката вычисляется один раз и сохраняется, поэтому повтор вебхука
     не отнимет период дважды;
   - пакет обхода или платеж без выданной подписки: доступ не трогаем, только
     алерт (решает админ).
4. Частичный возврат: только запись и алерт админу.
Админу всегда уходит сообщение о возврате (один раз на refund_id).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from html import escape as _he
from typing import Any, Dict, Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from app.config import settings
from app.logger import logger
from app.services.payments.errors import WebhookRetryableError

# Цель «истечь сейчас»: при каждом применении это now + REFUND_EXPIRE_GRACE
# (панель отклоняет expireAt в прошлом). "disable" — значение из ранней версии
# 2.1, трактуется так же.
REFUND_EXPIRE_NOW = "expire_now"
_LEGACY_DISABLE = "disable"
REFUND_EXPIRE_GRACE = timedelta(minutes=5)


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


async def _shorten_obhod(session, telegram_user_id: int, plan_code: Optional[str],
                         target: str, naive: Optional[datetime], refund_id: str) -> str:
    """Возврат продления Pro: обходу ставится тот же укороченный срок (ревью m2).

    Возвращает пометку для алерта админу ("" если все хорошо или трогать нечего).
    """
    from app.core.plans import is_obhod_eligible_plan

    if not is_obhod_eligible_plan(plan_code):
        return ""
    try:
        from app.services.obhod_service import get_obhod_subscription
        obhod = await get_obhod_subscription(session, telegram_user_id)
        if not obhod or not obhod.active or not obhod.remna_user_id:
            return ""
        from app.remnawave.client import RemnaClient

        client = RemnaClient()
        try:
            await client.update_user(str(obhod.remna_user_id), expire_at=target)
        finally:
            await client.close()
        obhod.valid_until = naive
        return " Срок обхода укорочен так же."
    except Exception as e:
        logger.warning(f"refund {refund_id}: obhod shorten failed: {e}")
        return f" Срок обхода укоротить НЕ удалось ({e}), поправьте вручную."


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
        can_revoke = (
            is_full
            and subscription is not None
            and getattr(subscription, "sub_kind", "main") == "main"
            and subscription.remna_user_id
            and not is_obhod_package_code(plan_code)
            and period_months > 0
        )

        from app.remnawave.client import RemnaClient, normalize_expire_at

        user_text = None
        new_expire_for_notices: Optional[datetime] = None
        if can_revoke:
            client = RemnaClient()
            try:
                target = entry.get("target_expire")
                if not target:
                    # Фиксируем цель один раз, чтобы повтор не отнял период дважды.
                    try:
                        data = await client.get_user_by_id(str(subscription.remna_user_id))
                    except Exception as e:
                        raise WebhookRetryableError(f"remnawave read failed: {e}") from e
                    raw = data.get("response", data) if isinstance(data, dict) else {}
                    current = _parse_expire((raw or {}).get("expireAt"))
                    now = datetime.now(timezone.utc)
                    if current is None:
                        target = REFUND_EXPIRE_NOW
                    else:
                        new_expire = current - relativedelta(months=period_months)
                        target = (
                            REFUND_EXPIRE_NOW if new_expire <= now + REFUND_EXPIRE_GRACE
                            else normalize_expire_at(new_expire)
                        )
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
                    if target in (REFUND_EXPIRE_NOW, _LEGACY_DISABLE):
                        expire_dt = datetime.now(timezone.utc) + REFUND_EXPIRE_GRACE
                        await client.update_user(
                            str(subscription.remna_user_id), expire_at=normalize_expire_at(expire_dt)
                        )
                        naive = expire_dt.replace(tzinfo=None)
                        new_expire_for_notices = expire_dt
                        subscription.active = False
                        subscription.valid_until = naive
                        subscription.remnawave_expected_expire_at = naive
                        subscription.provisioning_state = "expired"
                        subscription.last_provisioning_error = f"refund {refund_id}"
                        try:
                            from app.services.obhod_service import deactivate_obhod
                            await deactivate_obhod(session, payment.telegram_user_id, trace_id=f"refund_{refund_id}")
                        except Exception as e:
                            logger.warning(f"refund {refund_id}: obhod deactivate soft-fail: {e}")
                            obhod_note = f" Обход погасить не удалось ({e}), проверьте вручную."
                        action = "expired"
                        action_note = (
                            "Срок после отката уже в прошлом: юзеру поставлен expireAt = сейчас "
                            "(панель переведет в EXPIRED), подписка и обход погашены. "
                            "Повторная оплата или выдача снова включит доступ."
                        )
                        user_text = (
                            "↩️ <b>Возврат оформлен</b>\n\n"
                            "Деньги по платежу возвращены, доступ по этой оплате закончился. "
                            "Если захотите вернуться, оформите подписку в меню."
                        )
                    else:
                        await client.update_user(str(subscription.remna_user_id), expire_at=target)
                        new_dt = _parse_expire(target)
                        naive = new_dt.replace(tzinfo=None) if new_dt else None
                        new_expire_for_notices = new_dt
                        subscription.valid_until = naive
                        subscription.remnawave_expected_expire_at = naive
                        action = "shortened"
                        action_note = (
                            f"Срок откатан на {period_months} мес. от текущей даты окончания "
                            f"(подписка одна на юзера): до {naive:%d.%m.%Y}." if naive else ""
                        )
                        obhod_note = await _shorten_obhod(
                            session, payment.telegram_user_id, plan_code, target, naive, refund_id
                        )
                        user_text = (
                            "↩️ <b>Возврат оформлен</b>\n\n"
                            "Деньги по платежу возвращены, оплаченный период снят. "
                            + (f"Подписка действует до {naive:%d.%m.%Y}." if naive else "")
                        )
                except WebhookRetryableError:
                    raise
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
