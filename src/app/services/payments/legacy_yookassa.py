"""2.x payment code (release 3.0: legacy, deleted at the cutover).

Imported as ``app.services.payments.yookassa`` too (that module is an alias of
this one). What is still LIVE here in 3.0:

- provisioning core (``provision_paid_period``: Phase A/B/verify/Phase C in the
  database) behind the Foundation ProvisioningService shim
  (app.services.payments.legacy_provisioning) until stream B replaces it;
- ``resync_subscription_to_remnawave`` (2.x reconciler), ``generate_remna_password``;
- ``create_payment`` for obhod packages bought from the 2.x screens;
- ``check_payment_status`` (now over the async gateway, no SDK).

``process_payment_webhook`` / ``handle_successful_payment`` are the 2.x entry
points; production calls app.services.payments.webhook and
app.services.fulfillment instead. They stay for the 2.x tests of the shared
core until the cutover.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Callable, Union
from dataclasses import dataclass
from dateutil.relativedelta import relativedelta
import secrets
import string
import uuid
from sqlalchemy.exc import IntegrityError
from yookassa.domain.notification import WebhookNotification  # dead 2.x webhook body only
from app.config import settings
from app.logger import logger
from app.db.session import SessionLocal
from app.db.models import Payment as PaymentModel, Subscription, TelegramUser, RemnaUser
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.remnawave.client import RemnaClient, normalize_expire_at
from app.services.payments.errors import ProvisioningPendingError, WebhookRetryableError
from app.services.remna_tariff import RemnaUserDisabledError

# Допуск при сравнении ожидаемого vs фактического expireAt в Remnawave.
# 60s покрывает дрифт между моментом APIcall и моментом, когда Remnawave записал значение.
REMNA_EXPIRE_TOLERANCE_SECONDS = 60


def _device_limit_for_plan(plan_code: Optional[str]) -> int:
    """Возвращает лимит устройств (hwidDeviceLimit) по тарифу."""
    from app.core.plans import get_plan_device_limit
    return get_plan_device_limit(plan_code)


async def get_squad_name_for_plan(plan_code: Optional[str]) -> Optional[str]:
    """Возвращает имя сквада для плана подписки.

    Для unknown plan_code возвращает None — caller должен пометить provisioning failed.
    """
    from app.core.plans import get_plan_squad
    return get_plan_squad(plan_code)


def generate_remna_password(length: int = 24) -> str:
    """
    Генерирует безопасный пароль для Remna API
    
    Требования Remna API:
    - Минимум 24 символа
    - Должен содержать заглавные и строчные буквы
    - Должен содержать цифры
    """
    if length < 24:
        length = 24
    
    # Гарантируем наличие всех требуемых типов символов
    uppercase = secrets.choice(string.ascii_uppercase)
    lowercase = secrets.choice(string.ascii_lowercase)
    digits = secrets.choice(string.digits)
    
    # Генерируем остальные символы
    all_chars = string.ascii_letters + string.digits
    remaining = ''.join(secrets.choice(all_chars) for _ in range(length - 3))
    
    # Смешиваем все символы
    password_chars = list(uppercase + lowercase + digits + remaining)
    secrets.SystemRandom().shuffle(password_chars)
    
    return ''.join(password_chars)

# Допустимые переходы статусов платежа (FSM)
VALID_STATUS_TRANSITIONS = {
    "pending": {"succeeded", "canceled", "failed", "waiting_for_capture"},
    "waiting_for_capture": {"succeeded", "canceled", "failed"},
    "succeeded": set(),  # терминальный
    "canceled": set(),
    "failed": set(),
    "refunded": set(),  # терминальный: после возврата payment.succeeded не принимается
}


async def _create_yookassa_payment(payment_data: Dict[str, Any], idempotence_key: str) -> Dict[str, Any]:
    """POST /payments over the async client (3.0: no SDK). Returns provider JSON."""
    from app.infra.yookassa import default_gateway

    return await default_gateway().client().create_payment(payment_data, idempotence_key)


def _safe_response(p: Dict[str, Any]) -> Dict[str, Any]:
    """Provider response for payment_metadata without the confirmation URL."""
    return {k: p.get(k) for k in ("id", "status", "paid", "amount", "created_at", "test") if k in p}


async def create_payment(
    amount_rub: Optional[int] = None,
    description: str = "CRS VPN",
    user_id: int = 0,
    plan_code: Optional[str] = None,
    period_months: Optional[int] = None,
    request_id: Optional[int] = None,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> tuple[str, str]:
    """Создает платеж в YooKassa и возвращает (payment_url, external_id).

    Сумму считает САМ по services/checkout.resolve_purchase_amount (каталог +
    право юзера на тариф). amount_rub от вызывающего не нужен; если передан и
    не совпал с серверной ценой — ValueError, платеж не создается.
    """
    trace_id = str(uuid.uuid4())
    if not user_id:
        raise ValueError("create_payment: user_id обязателен")
    try:
        # Стоп-лист: не продаём тем, кого внесли вручную (см. app/services/blocklist.py)
        from app.services.blocklist import get_user_block_reason, notify_admins
        block_reason = await get_user_block_reason(user_id)
        if block_reason is not None:
            logger.warning(
                f"[{trace_id}] blocked_user_payment_attempt: tg_id={user_id} "
                f"plan={plan_code} amount={amount_rub} reason={block_reason}"
            )
            await notify_admins(
                f"\u26d4\ufe0f <b>Заблокированный пользователь пытался оплатить</b>\n"
                f"ID: <code>{user_id}</code>\n"
                f"Тариф: {plan_code or '-'}, сумма: {amount_rub}\u20bd\n"
                f"Причина блокировки: {block_reason or '-'}"
            )
            raise ValueError("Оплата недоступна для этого аккаунта")

        # Хотфикс 2.1: сумма обязана совпадать с прайсом. Любой caller (кнопка
        # тарифа, пакет обхода) не может создать платеж с произвольной суммой.
        from app.core.plans import amounts_match
        from app.services.checkout import resolve_purchase_amount
        expected_amount = await resolve_purchase_amount(
            plan_code, period_months, user_id, allow_obhod_package=True
        )
        if expected_amount <= 0 or (
            amount_rub is not None and not amounts_match(amount_rub, expected_amount)
        ):
            logger.warning(
                f"[{trace_id}] create_payment price mismatch: tg_id={user_id} plan={plan_code} "
                f"period={period_months} amount={amount_rub} expected={expected_amount}"
            )
            raise ValueError("Тариф недоступен для покупки")
        amount_rub = int(expected_amount)

        if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_API_KEY:
            raise ValueError("YOOKASSA_SHOP_ID и YOOKASSA_API_KEY должны быть настроены")
        
        api_key = str(settings.YOOKASSA_API_KEY).strip()
        if not api_key:
            raise ValueError("YOOKASSA_API_KEY пустой")
        
        if not settings.YOOKASSA_RETURN_URL:
            raise ValueError("YOOKASSA_RETURN_URL должен быть настроен")
        
        metadata = {"tg_user_id": user_id}
        if plan_code:
            metadata["plan_code"] = plan_code
        if period_months:
            metadata["period_months"] = str(period_months)
        if request_id:
            metadata["request_id"] = str(request_id)
        # Цена по прайсу на момент создания: вебхук сверяет оплаченную сумму с ней
        # (или с текущим прайсом), чтобы смена цен не ломала платежи «в полете».
        metadata["expected_amount"] = str(int(expected_amount))
        
        payment_data = {
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": str(settings.YOOKASSA_RETURN_URL)},
            "description": description,
            "metadata": metadata,
            "payment_method_types": ["bank_card", "sbp", "yoo_money"]
        }
        
        # Детерминированный idempotence_key: user+план+сумма+10-мин. окно.
        # Повторный вызов с теми же параметрами в течение 10 минут вернет тот же платеж YooKassa.
        _time_bucket = int(datetime.utcnow().timestamp()) // 600
        idempotence_key = f"cp_{user_id}_{plan_code or 'x'}_{period_months or 0}_{amount_rub}_{_time_bucket}"

        logger.info(
            f"[{trace_id}] calling YooKassa create payment: user={user_id} amount={amount_rub} "
            f"plan={plan_code} period={period_months} idempotence_key={idempotence_key}"
        )
        try:
            p = await _create_yookassa_payment(payment_data, idempotence_key)
            payment_id = p["id"]
            payment_url = (p.get("confirmation") or {}).get("confirmation_url")
        except Exception as api_error:
            error_msg = str(api_error)
            if "invalid_credentials" in error_msg.lower() or "password format" in error_msg.lower() or "unauthorized" in error_msg.lower():
                logger.error(f"[{trace_id}] Ошибка авторизации YooKassa: Проверьте YOOKASSA_API_KEY")
                raise ValueError("Ошибка авторизации YooKassa: проверьте правильность API ключа")
            elif "shop_id" in error_msg.lower() or "account_id" in error_msg.lower():
                logger.error(f"[{trace_id}] Ошибка YooKassa: Проверьте YOOKASSA_SHOP_ID")
                raise ValueError("Ошибка YooKassa: проверьте правильность Shop ID")
            else:
                logger.error(f"[{trace_id}] Ошибка API YooKassa: {error_msg}")
                raise
        
        if not SessionLocal:
            logger.error(f"[{trace_id}] payment created pending: id={payment_id} user={user_id} amount={amount_rub} — БД не настроена")
            raise ValueError("БД не настроена, платеж не может быть сохранен")
        
        payment_metadata = {
            "payment_data": payment_data,
            "yookassa_response": _safe_response(p),
            "trace_id": trace_id,
            "plan_code": plan_code,
            "period_months": period_months,
            "expected_amount": int(expected_amount),
        }
        
        for attempt in range(2):
            try:
                async with SessionLocal() as session:
                    # Гарантируем запись в telegram_users перед FK-зависимым insert в payments.
                    # Если username/first_name переданы — обновляем их при конфликте.
                    insert_vals: dict = {"telegram_id": user_id}
                    if username:
                        insert_vals["username"] = username
                    if first_name:
                        insert_vals["first_name"] = first_name
                    if last_name:
                        insert_vals["last_name"] = last_name
                    upsert_stmt = pg_insert(TelegramUser).values(**insert_vals)
                    if username or first_name or last_name:
                        update_set = {k: v for k, v in insert_vals.items() if k != "telegram_id"}
                        upsert_stmt = upsert_stmt.on_conflict_do_update(
                            index_elements=["telegram_id"], set_=update_set
                        )
                    else:
                        upsert_stmt = upsert_stmt.on_conflict_do_nothing(index_elements=["telegram_id"])
                    await session.execute(upsert_stmt)

                    result = await session.execute(
                        select(PaymentModel).where(PaymentModel.external_id == payment_id)
                    )
                    existing_payment = result.scalar_one_or_none()

                    if not existing_payment:
                        new_payment = PaymentModel(
                            telegram_user_id=user_id,
                            provider="yookassa",
                            external_id=payment_id,
                            amount=amount_rub,
                            currency="RUB",
                            status=p.get("status") or "pending",
                            description=description,
                            payment_metadata=payment_metadata,
                        )
                        session.add(new_payment)
                        await session.commit()
                        logger.info(
                            f"[{trace_id}] payment created pending: external_id={payment_id} user={user_id} "
                            f"amount={amount_rub} plan={plan_code} period={period_months}"
                        )
                    else:
                        logger.info(f"[{trace_id}] payment already exists: external_id={payment_id}")
                    break
            except IntegrityError as e:
                if "external_id" in str(e).lower() or "unique" in str(e).lower():
                    logger.warning(f"[{trace_id}] payment race: external_id={payment_id} already in DB, attempt={attempt}")
                    if attempt == 0:
                        await asyncio.sleep(0.1)
                        continue
                logger.error(f"[{trace_id}] Ошибка при сохранении платежа (IntegrityError): {e}")
                raise
            except Exception as e:
                logger.error(f"[{trace_id}] Ошибка при сохранении платежа (external_id={payment_id} user={user_id}): {e}")
                raise
        
        return (payment_url, payment_id)
        
    except Exception as e:
        logger.error(f"[{trace_id}] Ошибка при создании платежа: {e}")
        raise


async def process_payment_webhook(webhook_data: Dict[str, Any], bot) -> bool:
    """Обрабатывает webhook от YooKassa.

    Идемпотентность:
      - верхний слой — маркер yk_event:<event>:<id> (services/payments/webhook_dedup):
        дубль уже обработанного события -> True без работы; дубль, пока первая
        доставка еще идет -> WebhookRetryableError (503, YooKassa повторит);
        неуспех снимает маркер, повтор после 503 обрабатывается заново (Д-2);
      - нижний слой — `with_for_update()` на payment record + FSM VALID_STATUS_TRANSITIONS
        + гейт provisioning_state='synced'.
    """
    trace_id = str(uuid.uuid4())
    if not webhook_data:
        logger.error(f"[{trace_id}] webhook received: empty body")
        return False
    event = webhook_data.get("event")
    if not event:
        logger.error(f"[{trace_id}] webhook received: missing event")
        return False

    from app.services.payments.webhook_dedup import run_webhook_once

    return await run_webhook_once(
        webhook_data, event, trace_id,
        lambda: _process_payment_webhook_body(webhook_data, bot, trace_id, event),
    )


async def _process_payment_webhook_body(
    webhook_data: Dict[str, Any], bot, trace_id: str, event: str
) -> bool:
    """Тело обработки вебхука (после дедупа). True = обработан, повтор не нужен."""
    try:
        logger.info(f"[{trace_id}] webhook received: event={event}")
        
        notification = WebhookNotification(webhook_data)
        payment = notification.object
        
        if not payment:
            logger.error(f"[{trace_id}] webhook received: no payment object")
            return False
        
        payment_id = payment.id

        # Стоп-лист по карте: деньги уже списаны, поэтому выдачу не рвём,
        # только сигналим админам — решение о возврате принимает человек.
        try:
            from app.services.blocklist import (
                card_fingerprint,
                get_card_block_reason,
                notify_admins,
            )

            _card = ((webhook_data.get("object") or {}).get("payment_method") or {}).get("card")
            _fp = card_fingerprint(_card)
            _card_reason = await get_card_block_reason(_fp)
            if _card_reason is not None:
                logger.warning(
                    f"[{trace_id}] blocked_card_payment: external_id={payment_id} "
                    f"fingerprint={_fp} reason={_card_reason}"
                )
                await notify_admins(
                    f"\u26a0\ufe0f <b>Оплата с карты из стоп-листа</b>\n"
                    f"Платёж: <code>{payment_id}</code>\n"
                    f"Карта: <code>{_fp}</code>\n"
                    f"Причина: {_card_reason or '-'}\n\n"
                    f"Подписка выдана штатно. Решай по возврату вручную."
                )
        except Exception as _bl_err:
            logger.warning(f"[{trace_id}] blocklist card check failed: {_bl_err}")

        webhook_metadata = payment.metadata or {}
        description = payment.description

        # Webhook is a trigger only. Always verify payment status directly with YooKassa API.
        logger.info(f"[{trace_id}] webhook trigger: external_id={payment_id} — verifying via YooKassa API")
        api_data = await check_payment_status(payment_id)
        if not api_data:
            logger.error(f"[{trace_id}] API verification failed: external_id={payment_id}")
            # YooKassa API недоступен: вебхук должен прийти повторно (503).
            raise WebhookRetryableError(f"YooKassa API verification unavailable for {payment_id}")
        if api_data.get("error") == "not_found":
            logger.warning(f"[{trace_id}] payment not found in YooKassa API: external_id={payment_id}")
            return False

        # Use API-verified values as source of truth
        status = api_data["status"]
        amount = float(api_data["amount"])
        currency = api_data.get("currency", "RUB")
        description = api_data.get("description") or description
        metadata = api_data.get("metadata") or webhook_metadata
        telegram_user_id = metadata.get("tg_user_id")

        # Fallback: tg_user_id missing from metadata — look up local Payment record by external_id.
        # This covers edge cases: manual payments created in YooKassa dashboard, test payments,
        # or any flow that didn't set metadata correctly.
        if not telegram_user_id and SessionLocal:
            async with SessionLocal() as _fb_session:
                _fb_result = await _fb_session.execute(
                    select(PaymentModel).where(PaymentModel.external_id == payment_id)
                )
                _fb_payment = _fb_result.scalar_one_or_none()
                if _fb_payment:
                    telegram_user_id = str(_fb_payment.telegram_user_id)
                    logger.warning(
                        f"[{trace_id}] webhook: tg_user_id missing from metadata, resolved from local DB: "
                        f"payment_id={payment_id} tg_user_id={telegram_user_id} "
                        f"amount={amount} status={status}"
                    )

        if not telegram_user_id:
            logger.error(
                f"[{trace_id}] webhook: user cannot be resolved — "
                f"payment_id={payment_id} status={status} amount={amount} "
                f"metadata={metadata!r} description={description!r}"
            )
            return False

        telegram_user_id = int(telegram_user_id)
        
        from app.services.users import get_or_create_telegram_user
        try:
            await get_or_create_telegram_user(
                telegram_id=telegram_user_id,
                username=None,
                first_name=None,
                last_name=None,
                language_code=None,
            )
        except Exception as e:
            logger.warning(f"[{trace_id}] get_or_create_telegram_user failed: user={telegram_user_id} err={e}")
        
        plan_code = metadata.get("plan_code") or metadata.get("tariff")
        logger.info(
            f"[{trace_id}] webhook verified: payment_id={payment_id} status={status} "
            f"tg_user_id={telegram_user_id} amount={amount} {currency} tariff={plan_code}"
        )
        
        if not SessionLocal:
            logger.error(f"[{trace_id}] БД не настроена, платеж не может быть обработан")
            return False
        
        async with SessionLocal() as session:
            stmt = (
                select(PaymentModel)
                .where(PaymentModel.external_id == payment_id)
                .with_for_update()  # Blocking: concurrent duplicate webhooks wait, then see updated state
            )
            result = await session.execute(stmt)
            existing_payment = result.scalar_one_or_none()
            
            payment_db = None
            if existing_payment:
                old_status = existing_payment.status
                if old_status == status:
                    logger.info(f"[{trace_id}] payment unchanged: external_id={payment_id} status={status}")
                    payment_db = existing_payment
                else:
                    allowed = VALID_STATUS_TRANSITIONS.get(old_status)
                    if allowed is None:
                        logger.error(
                            f"[{trace_id}] payment has unknown status in DB, refusing to transition: "
                            f"external_id={payment_id} old_status={old_status!r} -> {status!r}"
                        )
                        return False
                    if status in allowed:
                        existing_payment.status = status
                        existing_payment.updated_at = datetime.utcnow()
                        existing_payment.amount = amount
                        if status == "succeeded" and not existing_payment.paid_at:
                            existing_payment.paid_at = datetime.utcnow()
                        meta = existing_payment.payment_metadata or {}
                        if isinstance(meta, dict):
                            meta = dict(meta)
                        meta["last_webhook"] = webhook_data
                        meta["trace_id"] = trace_id
                        existing_payment.payment_metadata = meta
                        await session.commit()
                        payment_db = existing_payment
                        logger.info(f"[{trace_id}] payment updated: external_id={payment_id} {old_status} -> {status}")
                    else:
                        logger.warning(
                            f"[{trace_id}] payment invalid transition: external_id={payment_id} "
                            f"{old_status} -> {status} (ignored)"
                        )
                        payment_db = existing_payment
            else:
                try:
                    meta = dict(webhook_data) if isinstance(webhook_data, dict) else {}
                    meta["trace_id"] = trace_id
                    meta["plan_code"] = metadata.get("plan_code")
                    meta["period_months"] = metadata.get("period_months")
                    if metadata.get("expected_amount") is not None:
                        meta["expected_amount"] = metadata.get("expected_amount")
                    new_payment = PaymentModel(
                        telegram_user_id=telegram_user_id,
                        provider="yookassa",
                        external_id=payment_id,
                        amount=amount,
                        currency=currency,
                        status=status,
                        description=description,
                        payment_metadata=meta,
                    )
                    if status == "succeeded":
                        new_payment.paid_at = datetime.utcnow()
                    session.add(new_payment)
                    await session.commit()
                    await session.refresh(new_payment)
                    payment_db = new_payment
                    logger.info(f"[{trace_id}] payment created from webhook: external_id={payment_id} status={status}")
                except IntegrityError as e:
                    if "external_id" in str(e).lower() or "unique" in str(e).lower():
                        await session.rollback()
                        result = await session.execute(
                            select(PaymentModel).where(PaymentModel.external_id == payment_id)
                        )
                        payment_db = result.scalar_one_or_none()
                        if payment_db:
                            payment_db.status = status
                            payment_db.updated_at = datetime.utcnow()
                            payment_db.amount = amount
                            if status == "succeeded" and not payment_db.paid_at:
                                payment_db.paid_at = datetime.utcnow()
                            await session.commit()
                            logger.info(f"[{trace_id}] payment race resolved: external_id={payment_id} updated from webhook")
                        else:
                            logger.error(f"[{trace_id}] IntegrityError and payment not found: {e}")
                            return False
                    else:
                        raise
            
            if payment_db and payment_db.status == "succeeded":
                # Идемпотентность по реальному состоянию sync, а не по наличию
                # subscription_id. subscription_id может быть выставлен, но Remnawave
                # не подтвердил sync (split-state) — тогда повторный webhook должен
                # дотолкать sync, а не делать skip.
                already_synced = False
                if payment_db.subscription_id:
                    sub_r = await session.execute(
                        select(Subscription).where(Subscription.id == payment_db.subscription_id)
                    )
                    sub_for_check = sub_r.scalar_one_or_none()
                    if sub_for_check and sub_for_check.provisioning_state == "synced":
                        already_synced = True

                if already_synced:
                    logger.info(
                        f"[{trace_id}] idempotent skip: payment fully provisioned "
                        f"external_id={payment_id} subscription_id={payment_db.subscription_id}"
                    )
                else:
                    from app.services.cache import acquire_provision_lock, release_provision_lock
                    lock_acquired = await acquire_provision_lock(payment_id)
                    if not lock_acquired:
                        logger.info(
                            f"[{trace_id}] provision_lock busy: another process is provisioning "
                            f"external_id={payment_id} — skipping, recovery will handle if needed"
                        )
                    else:
                        try:
                            await handle_successful_payment(
                                session=session,
                                payment_id=payment_db.id,
                                telegram_user_id=telegram_user_id,
                                amount=amount,
                                description=description or "CRS VPN 30 дней",
                                bot=bot,
                                trace_id=trace_id,
                            )
                        finally:
                            await release_provision_lock(payment_id)

        return True

    except (ProvisioningPendingError, WebhookRetryableError):
        # Pre-marked as failed in handle_successful_payment. Propagate so the webhook
        # endpoint can answer 5xx and YooKassa will retry; reconciler is the safety net.
        raise
    except Exception as e:
        ext_id = "?"
        try:
            obj = webhook_data.get("object", {}) if webhook_data else {}
            ext_id = obj.get("id", "?") if isinstance(obj, dict) else getattr(obj, "id", "?")
        except Exception:
            pass
        logger.error(f"[{trace_id}] webhook error: external_id={ext_id} err={e}")
        import traceback
        logger.debug(traceback.format_exc())
        raise


async def _mark_provisioning_failed(
    session,
    subscription_id: int,
    error: str,
    trace_id: str,
) -> None:
    """Помечает подписку failed и логирует. Не raise."""
    try:
        sub_r = await session.execute(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        sub = sub_r.scalar_one_or_none()
        if sub:
            sub.provisioning_state = "failed"
            sub.last_provisioning_error = error[:500]
            sub.last_provisioning_attempt_at = datetime.utcnow()
            await session.commit()
            logger.error(
                f"[{trace_id}] provisioning_remnawave_sync_failed: "
                f"subscription_id={subscription_id} state=failed error={error[:200]!r}"
            )
    except Exception as e:
        logger.error(f"[{trace_id}] _mark_provisioning_failed: {e}")


async def _verify_remnawave_synced(
    remna_user_id: str,
    expected_expire_at: Optional[datetime],
    trace_id: str,
    plan_code: Optional[str] = None,
    allow_disabled: bool = False,
) -> tuple[bool, Optional[datetime], Optional[str]]:
    """Перечитывает юзера из Remnawave и проверяет, что expireAt близок к expected.

    Returns (ok, actual_expire_at, error_message).
    - ok=True если status в (ACTIVE, LIMITED) и |actual - expected| <= tolerance.
    - ok=False с error_message при любом расхождении / недоступности.

    Tolerance — REMNA_EXPIRE_TOLERANCE_SECONDS. expected_expire_at=None → проверяем
    только что юзер существует и НЕ EXPIRED.
    """
    client = RemnaClient()
    try:
        try:
            data = await client.get_user_by_id(str(remna_user_id))
        except Exception as e:
            return False, None, f"get_user_by_id failed: {e}"
        raw = data.get("response", data) if isinstance(data, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        status = raw.get("status")
        expire_raw = raw.get("expireAt")
        actual: Optional[datetime] = None
        if expire_raw:
            try:
                expire_str = str(expire_raw).replace("Z", "+00:00")
                actual = datetime.fromisoformat(expire_str)
                if actual.tzinfo is not None:
                    actual = actual.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception as e:
                return False, None, f"could not parse expireAt={expire_raw!r}: {e}"

        if status == "EXPIRED":
            return False, actual, f"remnawave status={status} (expected ACTIVE/LIMITED)"
        # Ревью M1: DISABLED юзер не пускается нодами. Для оплаты это «не выдано»
        # (выдача сама включает юзера, см. remna_tariff). Resync реконсилера
        # (allow_disabled=True) ручное отключение админом не оспаривает.
        if status == "DISABLED" and not allow_disabled:
            return False, actual, "remnawave status=DISABLED (expected ACTIVE/LIMITED)"

        # Хотфикс 2.1: «synced» только если сквад тарифа реально стоит у юзера.
        # Иначе оплаченный юзер без сквада не видит ни одной ноды, а подписка
        # считалась выданной и реконсилер ее больше не трогал.
        if plan_code:
            from app.core.plans import get_plan_squad
            from app.services.remna_tariff import extract_squad_uuids
            squad_name = get_plan_squad(plan_code)
            if not squad_name:
                return False, actual, f"unknown plan_code={plan_code!r}: нет сквада в каталоге"
            try:
                squads = await client.list_internal_squads()
            except Exception as e:
                return False, actual, f"internal squads lookup failed: {e}"
            target = next(
                (sq.get("uuid") for sq in squads if isinstance(sq, dict) and sq.get("name") == squad_name),
                None,
            )
            if not target:
                return False, actual, f"squad {squad_name!r} not found in Remnawave"
            if target not in extract_squad_uuids(raw):
                return False, actual, f"plan squad {squad_name!r} missing on remna user {remna_user_id}"

        if expected_expire_at is not None:
            if actual is None:
                return False, None, "remnawave expireAt is missing but expected was set"
            # expected_expire_at — UTC naive (см. handle_successful_payment), actual тоже сделали naive
            expected_naive = (
                expected_expire_at.astimezone(timezone.utc).replace(tzinfo=None)
                if expected_expire_at.tzinfo
                else expected_expire_at
            )
            # Асимметричная проверка: actual >= expected - tolerance.
            # actual > expected — НЕ ошибка: подписка может уже быть продлена дальше
            # (легитимное накопление от старых платежей / админских грантов / friend-grant).
            # Сравнивать симметрично (abs) опасно: при retry storm каждый _compute_extend_expire_str
            # добавлял +period к Remnawave, а verify ловил расхождение и снова крутил retry —
            # подписка уехала бы в годы вперёд (был такой баг, починен).
            shortfall = (expected_naive - actual).total_seconds()
            if shortfall > REMNA_EXPIRE_TOLERANCE_SECONDS:
                return (
                    False,
                    actual,
                    f"expireAt shortfall: actual={actual.isoformat()} "
                    f"expected={expected_naive.isoformat()} shortfall={shortfall:.0f}s "
                    f"tolerance={REMNA_EXPIRE_TOLERANCE_SECONDS}s",
                )

        return True, actual, None
    finally:
        try:
            await client.close()
        except Exception:
            pass


async def resync_subscription_to_remnawave(
    subscription_id: int,
    trace_id: Optional[str] = None,
) -> bool:
    """Повторный sync подписки с Remnawave (для reconciler'а).

    Не отправляет уведомления юзеру/админу — только синкает Remnawave и проставляет
    provisioning_state. Возвращает True при успехе, False при провале.
    """
    trace_id = trace_id or str(uuid.uuid4())
    if not SessionLocal:
        return False

    async with SessionLocal() as session:
        sub_r = await session.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        )
        subscription = sub_r.scalar_one_or_none()
        if not subscription:
            logger.warning(f"[{trace_id}] resync: subscription not found id={subscription_id}")
            return False

        if not subscription.active and not subscription.is_lifetime:
            logger.info(f"[{trace_id}] resync: subscription not active, skipping id={subscription_id}")
            return False

        subscription.last_provisioning_attempt_at = datetime.utcnow()
        await session.commit()

        # Вычисляем expected expire: для не-lifetime — valid_until.
        expected_expire = subscription.valid_until if not subscription.is_lifetime else None

        # period_months=None: get_or_create_remna_user_and_get_subscription_url пойдет по
        # fallback-ветке "обновить expireAt = subscription.valid_until".
        try:
            subscription_url = await get_or_create_remna_user_and_get_subscription_url(
                telegram_user_id=subscription.telegram_user_id,
                subscription_id=subscription.id,
                period_months=None,
            )
        except Exception as e:
            await _mark_provisioning_failed(session, subscription.id, f"resync sync error: {e}", trace_id)
            return False

        # Перечитываем подписку (могла обновиться внутри get_or_create_...).
        # populate_existing: иначе identity map отдаст устаревший объект (фикс B1).
        sub_r = await session.execute(
            select(Subscription)
            .where(Subscription.id == subscription_id)
            .execution_options(populate_existing=True)
        )
        subscription = sub_r.scalar_one_or_none()
        if not subscription:
            return False

        remna_user_id = subscription.remna_user_id
        if not remna_user_id:
            tg_r = await session.execute(
                select(TelegramUser)
                .where(TelegramUser.telegram_id == subscription.telegram_user_id)
                .execution_options(populate_existing=True)
            )
            tg = tg_r.scalar_one_or_none()
            remna_user_id = tg.remna_user_id if tg else None

        if not remna_user_id:
            await _mark_provisioning_failed(
                session, subscription.id, "resync: remna_user_id still missing", trace_id
            )
            return False

        ok, actual, err = await _verify_remnawave_synced(
            remna_user_id, expected_expire, trace_id, plan_code=subscription.plan_code,
            allow_disabled=True,
        )
        if not ok:
            await _mark_provisioning_failed(session, subscription.id, f"resync verify: {err}", trace_id)
            return False

        subscription.provisioning_state = "synced"
        subscription.remnawave_synced_at = datetime.utcnow()
        subscription.remnawave_expected_expire_at = expected_expire
        subscription.last_provisioning_error = None
        if subscription_url:
            cfg = dict(subscription.config_data or {})
            cfg["subscription_url"] = subscription_url
            subscription.config_data = cfg
        await session.commit()
        logger.info(
            f"[{trace_id}] reconciler_resync_succeeded: subscription_id={subscription.id} "
            f"tg_id={subscription.telegram_user_id} expire={expected_expire}"
        )
        return True


_PROVISION_ALERT_TTL_SECONDS = 6 * 3600

DISABLED_USER_REVIEW_REASON = (
    "пользователь отключен вручную в панели (DISABLED), оплата получена, реши вручную: "
    "«Одобрить и выдать» включит юзера и выдаст оплаченный срок"
)


async def _remna_user_is_disabled(remna_user_id: str, trace_id: str) -> bool:
    """True, если юзер в панели DISABLED. Ошибка чтения -> False (дальше выдача
    сама проверит статус и откажет, см. RemnaUserDisabledError)."""
    client = RemnaClient()
    try:
        data = await client.get_user_by_id(str(remna_user_id))
    except Exception as e:
        logger.debug(f"[{trace_id}] disabled check for {remna_user_id} failed: {e}")
        return False
    finally:
        try:
            await client.close()
        except Exception:
            pass
    raw = data.get("response", data) if isinstance(data, dict) else {}
    return isinstance(raw, dict) and str(raw.get("status") or "").upper() == "DISABLED"


async def _alert_paid_not_provisioned(
    bot, payment_id: int, telegram_user_id: int, error: str, trace_id: str
) -> None:
    """Один алерт админам на платеж (Redis SET NX, 6 ч): оплачено, но выдача не прошла."""
    from html import escape as _he

    from app.services.redis_flags import set_once

    first = await set_once(
        f"alert:paid_not_provisioned:{payment_id}", trace_id or "1",
        ttl=_PROVISION_ALERT_TTL_SECONDS,
    )
    if first is False:
        return

    text = (
        "⚠️ <b>Оплата есть, доступ не выдан</b>\n\n"
        f"Telegram ID: <code>{telegram_user_id}</code>\n"
        f"Payment row id: <code>{payment_id}</code>\n"
        f"Ошибка: <code>{_he(error[:300])}</code>\n\n"
        "Подписка помечена failed, бот повторит выдачу автоматически "
        "(повтор вебхука, recovery, реконсилер). Если не пройдет, проверьте "
        "сквады и юзера в Remnawave."
    )
    for admin_id in (settings.ADMINS or []):
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[{trace_id}] provisioning alert to admin {admin_id} failed: {e}")


# 3.0: правило вынесено в app.services.payments.pricing (одно на оба пути).
from app.services.payments.pricing import price_mismatch_reason as _price_mismatch_reason  # noqa: E402


async def _hold_payment_for_review(
    session,
    payment,
    telegram_user_id: int,
    reason: str,
    bot,
    trace_id: str,
) -> None:
    """Платеж не провижиним: помечаем needs_review и один раз шлем алерт админам и юзеру.

    Снять с ревью может только человек: выставить payment_metadata.review_approved=true
    (тогда recovery/кнопка проверки проведут выдачу) или оформить возврат.
    """
    from html import escape as _he

    meta = dict(payment.payment_metadata or {}) if isinstance(payment.payment_metadata, dict) else {}
    already_alerted = bool(meta.get("review_alerted"))
    meta["needs_review"] = True
    meta["review_reason"] = reason[:500]
    meta.setdefault("review_marked_at", datetime.utcnow().isoformat())
    payment.payment_metadata = meta
    await session.commit()
    logger.error(
        f"[{trace_id}] payment_held_for_review: payment_id={payment.id} "
        f"external_id={payment.external_id} tg_id={telegram_user_id} reason={reason}"
    )
    if already_alerted:
        return

    alerted = False
    admin_text = (
        "🚨 <b>Платеж на ручной проверке</b>\n\n"
        f"Payment ID: <code>{_he(str(payment.external_id))}</code>\n"
        f"Telegram ID: <code>{telegram_user_id}</code>\n"
        f"Сумма: {_he(str(payment.amount))} {_he(str(payment.currency or ''))}\n"
        f"Причина: {_he(reason)}\n\n"
        "Подписка НЕ выдана. «Одобрить и выдать» проведет обычную выдачу, "
        "«Отклонить» оставит без доступа (возврат оформите в кабинете YooKassa)."
    )
    from app.keyboards import get_payment_review_keyboard, get_support_keyboard
    for admin_id in (settings.ADMINS or []):
        try:
            await bot.send_message(
                chat_id=admin_id, text=admin_text, parse_mode="HTML",
                reply_markup=get_payment_review_keyboard(payment.id),
            )
            alerted = True
        except Exception as e:
            logger.warning(f"[{trace_id}] review alert to admin {admin_id} failed: {e}")
    try:
        await bot.send_message(
            chat_id=telegram_user_id,
            text=(
                "⏳ <b>Оплата получена</b>\n\n"
                "Платеж передан на ручную проверку администратору. "
                "Мы свяжемся с вами в ближайшее время. Если есть вопросы, "
                "напишите в поддержку."
            ),
            parse_mode="HTML",
            reply_markup=get_support_keyboard(),
        )
    except Exception as e:
        logger.debug(f"[{trace_id}] review notice to user failed: {e}")
    if alerted:
        meta = dict(payment.payment_metadata or {})
        meta["review_alerted"] = True
        payment.payment_metadata = meta
        await session.commit()


class PaymentUserMissingError(Exception):
    """Нет telegram_users-строки плательщика: выдавать некому (2.x: return None)."""


@dataclass
class ProvisionResult:
    subscription: Any
    telegram_user: Any
    plan_name: str
    valid_until: datetime
    actual_expire_at: Optional[datetime]


async def provision_paid_period(
    session,
    *,
    payment,
    telegram_user_id: int,
    plan_code: str,
    period_months: Optional[int],
    delta: Union[relativedelta, timedelta, None] = None,
    review_approved: bool = False,
    trace_id: str,
    fixed_target: Optional[datetime] = None,
    on_target: Optional[Callable[[datetime], Any]] = None,
    reuse_unfinished_target: bool = True,
) -> ProvisionResult:
    """Ядро выдачи оплаченного срока (2.x Phase A/B/verify/C без ценового гейта
    и уведомлений). Общий код 2.x handle_successful_payment и 3.0
    LegacyProvisioningService.grant (app.services.payments.legacy_provisioning).

    Raises: PaymentUserMissingError, RemnaUserDisabledError (решает админ),
    ProvisioningPendingError (панель не подтвердила; повторить позже).
    Фиксы B1 (populate_existing) и B2 (база = max(now, панель, БД)) внутри.
    """
    payment_id = payment.id
    if delta is None:
        delta = relativedelta(months=int(period_months or 0))
    # Определяем plan_name через единый каталог
    from app.core.plans import get_plan_name
    plan_name = get_plan_name(plan_code)

    user_result = await session.execute(
        select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
    )
    telegram_user = user_result.scalar_one_or_none()

    if not telegram_user:
        logger.error(f"[{trace_id}] subscription_provisioning_failed: tg_id={telegram_user_id} not found in DB")
        raise PaymentUserMissingError(telegram_user_id)

    # Ревью N2: юзера, отключенного в панели вручную, оплата сама не включает.
    # Платеж уходит на ручную проверку; «Одобрить» (review_approved) включит
    # юзера и выдаст срок, «Отклонить» оставит как есть.
    if telegram_user.remna_user_id and not review_approved:
        if await _remna_user_is_disabled(str(telegram_user.remna_user_id), trace_id):
            raise RemnaUserDisabledError(DISABLED_USER_REVIEW_REASON)

    # Ищем ЛЮБУЮ подписку юзера, не только active=True. Причина:
    # `uq_subscriptions_telegram_user_id` — UNIQUE на telegram_user_id (без partial),
    # т.е. одна подписка на юзера ВСЕГДА. Если предыдущая попытка остановилась в
    # Phase B (active=False, provisioning_state='pending'/'failed'), мы должны ее
    # переиспользовать; новый INSERT упадет IntegrityError.
    # ВАЖНО (две подписки): фильтруем по sub_kind='main'. Иначе при наличии
    # obhod-строки scalar_one_or_none() упадет "more than one row".
    sub_result = await session.execute(
        select(Subscription).where(
            Subscription.telegram_user_id == telegram_user_id,
            Subscription.sub_kind == "main",
        )
    )
    existing_sub = sub_result.scalar_one_or_none()

    # Вычисляем целевой valid_until.
    # Idempotency: переиспользуем зафиксированный target ТОЛЬКО если это retry того же
    # незавершённого платежа (state in pending/failed). Если state=synced — это новый
    # платёж после успешной предыдущей оплаты, и старый remnawave_expected_expire_at
    # относится к ПРОШЛОЙ подписке, его использовать НЕЛЬЗЯ (мы бы записали старую дату
    # как valid_until новой оплаты).
    is_unfinished_retry = (
        reuse_unfinished_target
        and existing_sub is not None
        and existing_sub.remnawave_expected_expire_at is not None
        and existing_sub.provisioning_state in ("pending", "failed")
    )
    if fixed_target is not None:
        # 3.0: цель зафиксирована за ЭТИМ платежом (payment_metadata.grant_target),
        # чужой незавершенный target не переиспользуем (ревью round3 3d).
        valid_until = fixed_target
        logger.info(
            f"[{trace_id}] reusing fixed target of this payment: "
            f"tg_id={telegram_user_id} expected_expire={valid_until}"
        )
    elif is_unfinished_retry:
        valid_until = existing_sub.remnawave_expected_expire_at
        logger.info(
            f"[{trace_id}] reusing fixed target from prior attempt: "
            f"tg_id={telegram_user_id} expected_expire={valid_until} "
            f"state={existing_sub.provisioning_state}"
        )
    else:
        # Первая попытка по этому платежу (либо новый платёж после synced подписки).
        # Целевая дата = max(now, текущий expireAt в Remnawave) + period.
        # Учитываем текущее состояние Remnawave чтобы не сократить уже накопленный срок.
        # Фикс B2: если id панели у нас не записан (юзер с /start, /trial,
        # промо или гранта до фикса B1), ищем его по telegramId, как Phase B.
        # Раньше в этом случае база была now, и остаток триала/промо сгорал.
        base = datetime.utcnow()
        try:
            _client_peek = RemnaClient()
            try:
                if telegram_user.remna_user_id:
                    _peek = await _client_peek.get_user_by_id(str(telegram_user.remna_user_id))
                else:
                    _found = await _client_peek.get_user_by_telegram_id(telegram_user_id)
                    _peek = dict(_found.raw_data or {}) if _found else {}
            finally:
                try:
                    await _client_peek.close()
                except Exception:
                    pass
            _raw = _peek.get("response", _peek) if isinstance(_peek, dict) else {}
            if not isinstance(_raw, dict):
                _raw = {}
            _expire_raw = _raw.get("expireAt")
            if _expire_raw:
                _es = str(_expire_raw).replace("Z", "+00:00")
                _curr = datetime.fromisoformat(_es)
                if _curr.tzinfo is not None:
                    _curr = _curr.astimezone(timezone.utc).replace(tzinfo=None)
                if _curr > base:
                    base = _curr
                    logger.info(
                        f"[{trace_id}] extending from current remna expireAt: "
                        f"tg_id={telegram_user_id} current={_curr.isoformat()}"
                    )
        except Exception as _peek_e:
            logger.debug(f"[{trace_id}] could not peek remna expireAt: {_peek_e}")
        # Фикс B2: активная оплаченная подписка в БД тоже база (панель не
        # ответила на чтение, а срок у юзера есть: не начинаем с now).
        if (
            existing_sub is not None
            and existing_sub.active
            and existing_sub.valid_until is not None
            and existing_sub.valid_until > base
        ):
            base = existing_sub.valid_until
            logger.info(
                f"[{trace_id}] extending from DB valid_until: "
                f"tg_id={telegram_user_id} current={base.isoformat()}"
            )
        valid_until = base + delta
    if on_target is not None:
        await on_target(valid_until)

    # ===================== PHASE A: persist intent =====================
    # Записываем намерение: подписку с provisioning_state='pending'.
    # Для extension-кейса (existing_sub.active=True) НЕ обнуляем active/valid_until
    # на время Phase B — старая подписка остается валидной до подтверждения. Поле
    # remnawave_expected_expire_at несет новое целевое значение для верификации.
    if existing_sub:
        existing_sub.plan_code = plan_code
        existing_sub.plan_name = plan_name
        existing_sub.provisioning_state = "pending"
        existing_sub.remnawave_expected_expire_at = valid_until
        existing_sub.last_provisioning_attempt_at = datetime.utcnow()
        existing_sub.last_provisioning_error = None
        subscription = existing_sub
        logger.info(
            f"[{trace_id}] subscription extension intent: tg_id={telegram_user_id} "
            f"plan={plan_code} period={period_months}m current_valid_until={existing_sub.valid_until} "
            f"new_expected={valid_until}"
        )
    else:
        subscription = Subscription(
            telegram_user_id=telegram_user_id,
            plan_code=plan_code,
            plan_name=plan_name,
            active=False,  # Активируем только в Phase C, после подтверждения Remnawave
            valid_until=None,
            provisioning_state="pending",
            remnawave_expected_expire_at=valid_until,
            last_provisioning_attempt_at=datetime.utcnow(),
        )
        session.add(subscription)
        logger.info(
            f"[{trace_id}] subscription new intent: tg_id={telegram_user_id} "
            f"plan={plan_code} period={period_months}m expected_expire={valid_until}"
        )

    await session.commit()
    await session.refresh(subscription)

    # ===================== PHASE B: sync Remnawave =====================
    # На любой провал Remnawave: помечаем provisioning_state='failed' и raise.
    # Уведомления НЕ отправляются. Webhook вернет 503 и YooKassa повторит;
    # reconciler страхует, если повторов не будет.
    try:
        subscription_url = await get_or_create_remna_user_and_get_subscription_url(
            telegram_user_id=telegram_user_id,
            subscription_id=subscription.id,
            period_months=period_months,
            enable_if_disabled=review_approved,
        )
    except RemnaUserDisabledError as e:
        # Проверка выше не сработала (панель не ответила на чтение или юзера
        # отключили прямо сейчас): панель не тронута, решает админ.
        await _mark_provisioning_failed(
            session, subscription.id, f"remna_user_disabled: {e}", trace_id
        )
        raise
    except Exception as e:
        await _mark_provisioning_failed(
            session, subscription.id, f"remna_sync_exception: {e}", trace_id
        )
        raise ProvisioningPendingError(
            f"Remnawave sync raised: {e}"
        ) from e

    # Перечитываем подписку и telegram_user — get_or_create_... мог изменить
    # remna_user_id и subscription.config_data в своей сессии.
    # Фикс B1: объекты уже лежат в identity map этой сессии (expire_on_commit=False),
    # обычный SELECT вернул бы их со старыми атрибутами, и remna_user_id, записанный
    # Phase B в своей сессии, был бы не виден: первая оплата нового клиента
    # помечалась failed при уже обновленной панели. populate_existing перечитывает.
    subscription_id_for_failure = subscription.id  # сохраняем до reload, на случай гонки
    sub_r = await session.execute(
        select(Subscription)
        .where(Subscription.id == subscription_id_for_failure)
        .execution_options(populate_existing=True)
    )
    subscription = sub_r.scalar_one_or_none()
    tg_r = await session.execute(
        select(TelegramUser)
        .where(TelegramUser.telegram_id == telegram_user_id)
        .execution_options(populate_existing=True)
    )
    telegram_user = tg_r.scalar_one_or_none()

    if subscription is None:
        # Кто-то параллельно удалил подписку. Не пытаемся восстанавливать —
        # просто пробрасываем как pending; reconciler/recovery разберутся.
        logger.error(
            f"[{trace_id}] subscription disappeared after Phase B "
            f"id={subscription_id_for_failure} tg_id={telegram_user_id}"
        )
        raise ProvisioningPendingError(
            f"Subscription {subscription_id_for_failure} disappeared after Phase B"
        )

    remna_user_id_post = subscription.remna_user_id or (
        telegram_user.remna_user_id if telegram_user else None
    )
    if not remna_user_id_post or not subscription_url:
        await _mark_provisioning_failed(
            session,
            subscription.id,
            f"silent failure: remna_user_id={remna_user_id_post!r} "
            f"subscription_url={'set' if subscription_url else 'missing'}",
            trace_id,
        )
        raise ProvisioningPendingError(
            f"Remnawave silent failure: remna_user_id={remna_user_id_post!r} "
            f"url={'set' if subscription_url else 'missing'}"
        )

    # Верификация: перечитываем юзера из Remnawave и сравниваем expireAt.
    ok, actual_expire_at, verify_err = await _verify_remnawave_synced(
        remna_user_id_post, valid_until, trace_id, plan_code=plan_code
    )
    if not ok:
        await _mark_provisioning_failed(
            session, subscription.id, f"verification: {verify_err}", trace_id
        )
        raise ProvisioningPendingError(f"Remnawave verification failed: {verify_err}")

    # ===================== PHASE C: finalize =====================
    # Sync подтвержден. Активируем подписку, ставим payment.subscription_id, отмечаем
    # provisioning_state='synced'. ТОЛЬКО ПОСЛЕ ЭТОГО — уведомления.
    subscription.active = True
    subscription.valid_until = valid_until
    subscription.provisioning_state = "synced"
    subscription.remnawave_synced_at = datetime.utcnow()
    subscription.last_provisioning_error = None
    if subscription_url:
        cfg = dict(subscription.config_data or {})
        cfg["subscription_url"] = subscription_url
        subscription.config_data = cfg
    if not subscription.remna_user_id and remna_user_id_post:
        subscription.remna_user_id = remna_user_id_post

    # Сброс кэша last_plan, чтобы кнопка "🔄 Продлить" показала свежий план.
    try:
        from app.services.users import invalidate_last_plan_cache
        await invalidate_last_plan_cache(telegram_user_id)
    except Exception as _e:
        logger.debug(f"[{trace_id}] invalidate_last_plan_cache soft-fail: {_e}")

    payment_result = await session.execute(
        select(PaymentModel).where(PaymentModel.id == payment_id)
    )
    payment = payment_result.scalar_one_or_none()
    if payment:
        payment.subscription_id = subscription.id
        payment.status = "succeeded"
        if not payment.paid_at:
            payment.paid_at = datetime.utcnow()
        # Чистим устаревший needs_provisioning, если оставался от прошлой попытки.
        _meta = dict(payment.payment_metadata or {}) if isinstance(payment.payment_metadata, dict) else {}
        _meta.pop("needs_provisioning", None)
        _meta.pop("provisioning_error", None)
        payment.payment_metadata = _meta
    await session.commit()

    # ===== ОБХОД (две подписки): провижн obhod-юзера для Pro =====
    # Делаем ПОСЛЕ синка основной подписки. Не критично для основной выдачи:
    # ensure_obhod_for_pro глушит свои ошибки и не бросает наружу. Идемпотентно
    # переиспользует obhod-юзера при повторном webhook/recovery.
    try:
        from app.core.plans import is_obhod_eligible_plan
        if is_obhod_eligible_plan(plan_code):
            from app.services.obhod_service import ensure_obhod_for_pro
            await ensure_obhod_for_pro(
                session=session,
                telegram_user_id=telegram_user_id,
                plan_code=plan_code,
                valid_until=valid_until,
                trace_id=trace_id,
            )
        else:
            # Не-Pro: если у юзера был обход (был Pro, теперь даунгрейд) — гасим.
            from app.services.obhod_service import deactivate_obhod
            await deactivate_obhod(session, telegram_user_id, trace_id=trace_id)
    except Exception as _obhod_e:
        logger.warning(f"[{trace_id}] obhod provision soft-fail: {_obhod_e}")

    # Cache invalidation ПОСЛЕ commit — чтобы пользователь не увидел старый статус
    try:
        from app.services.cache import invalidate_subscription_cache, invalidate_sync_cache
        await invalidate_subscription_cache(telegram_user_id)
        await invalidate_sync_cache(telegram_user_id)
        logger.debug(f"[{trace_id}] cache invalidated after commit")
    except Exception as cache_e:
        logger.warning(f"[{trace_id}] cache invalidation failed: {cache_e}")

    return ProvisionResult(
        subscription=subscription,
        telegram_user=telegram_user,
        plan_name=plan_name,
        valid_until=valid_until,
        actual_expire_at=actual_expire_at,
    )


async def handle_successful_payment(
    session,
    payment_id: int,
    telegram_user_id: int,
    amount: float,
    description: str,
    bot,
    trace_id: Optional[str] = None,
) -> Optional[str]:
    """Обрабатывает успешный платеж: создает подписку и отправляет пользователю ссылку.

    Возвращает "review", если платеж задержан на ручную проверку (сумма не
    совпала с прайсом), иначе None.

    Фазы:
      A — записать intent: subscription с provisioning_state='pending', НЕ ставить
          payment.subscription_id. Если subscription уже active с прошлого раза, не
          сбрасываем active/valid_until до подтверждения Remnawave.
      B — sync Remnawave (get_or_create_remna_user_and_get_subscription_url) и
          верификация через get_user_by_id. При провале — provisioning_state='failed',
          raise ProvisioningPendingError (webhook вернет 503, юзер не будет уведомлен).
      C — финализация: provisioning_state='synced', payment.subscription_id, valid_until,
          уведомления юзеру/админу.
    """
    trace_id = trace_id or str(uuid.uuid4())

    try:
        payment_result = await session.execute(
            select(PaymentModel).where(PaymentModel.id == payment_id)
        )
        payment = payment_result.scalar_one_or_none()
        if not payment:
            logger.error(f"[{trace_id}] handle_successful_payment: payment not found id={payment_id}")
            return

        meta = payment.payment_metadata or {}

        # Guard: провижинить можно только реальные покупки YooKassa. Нулевые записи
        # promo/referral_payout/trial не несут plan_code — без этого guard они бы
        # ушли в AMOUNT FALLBACK ниже (0₽ → basic) и перезаписали юзеру тариф плюс
        # повторно отправили "оплата подтверждена". Реальное начисление по промо/
        # выплатам делает provision_tariff в remna_service, а не этот путь.
        if payment.provider and payment.provider != "yookassa":
            logger.warning(
                f"[{trace_id}] handle_successful_payment: skipping non-purchase "
                f"provider={payment.provider!r} payment_id={payment_id} "
                f"tg_id={telegram_user_id} amount={amount}"
            )
            return

        # Идемпотентность: блокируем платеж и проверяем, не была ли подписка уже
        # успешно засинкана. Гейт — provisioning_state='synced' (а не subscription_id),
        # это закрывает баг split-state когда subscription_id выставлен, но Remnawave
        # не обновлен.
        pay_locked = await session.execute(
            select(PaymentModel).where(PaymentModel.id == payment_id).with_for_update()
        )
        payment_locked = pay_locked.scalar_one_or_none()
        if not payment_locked:
            logger.error(f"[{trace_id}] handle_successful_payment: payment lost after lock id={payment_id}")
            return
        if payment_locked.subscription_id:
            sub_r = await session.execute(
                select(Subscription).where(Subscription.id == payment_locked.subscription_id)
            )
            existing_synced = sub_r.scalar_one_or_none()
            if existing_synced and existing_synced.provisioning_state == "synced":
                logger.info(
                    f"[{trace_id}] handle_successful_payment: already synced (idempotent) "
                    f"payment_id={payment_id} subscription_id={existing_synced.id}"
                )
                return
            logger.info(
                f"[{trace_id}] handle_successful_payment: re-provisioning, prior state="
                f"{getattr(existing_synced, 'provisioning_state', '?')!r} "
                f"payment_id={payment_id} subscription_id={payment_locked.subscription_id}"
            )

        logger.info(
            f"[{trace_id}] subscription_provisioning_started: payment_id={payment_id} "
            f"tg_id={telegram_user_id} amount={amount}"
        )

        # Получаем тариф и период из payment_metadata
        plan_code = None
        period_months = None
        if payment.payment_metadata:
            metadata = payment.payment_metadata
            if isinstance(metadata, dict):
                plan_code = metadata.get("plan_code")
                period_months = metadata.get("period_months")
                if period_months:
                    try:
                        period_months = int(period_months)
                    except (ValueError, TypeError):
                        period_months = None

        # ===== Платеж за ПАКЕТ ОБХОДА (а не за тариф) =====
        # plan_code здесь — код пакета (obhod_250/...). Это НЕ основная подписка:
        # поднимаем кап на существующем obhod-юзере и завершаем без provision'а main.
        from app.core.plans import is_obhod_package_code
        if is_obhod_package_code(plan_code):
            from app.services.obhod_service import apply_obhod_package

            # C1: идемпотентность ветки платежа-за-пакет по САМОМУ платежу.
            # Общий гейт already_synced тут не срабатывает (subscription_id
            # зануляется ниже), а redis-дедуп best-effort — поэтому при дубль-
            # доставке вебхука мы бы повторно подняли кап/период. Ранний выход,
            # если этот платеж уже был успешно применен.
            if isinstance(meta, dict) and meta.get("obhod_package_applied") is True:
                logger.info(
                    f"[{trace_id}] obhod package: платеж id={payment_id} уже применен "
                    f"(идемпотентный повтор вебхука) — skip tg_id={telegram_user_id}"
                )
                return

            _reason = _price_mismatch_reason(
                plan_code, period_months, amount, payment.currency, meta
            )
            if _reason and not (isinstance(meta, dict) and meta.get("review_approved")):
                await _hold_payment_for_review(
                    session, payment, telegram_user_id, _reason, bot, trace_id
                )
                return "review"

            applied = await apply_obhod_package(
                session=session,
                telegram_user_id=telegram_user_id,
                package_code=plan_code,
                trace_id=trace_id,
                payment_id=payment.id,
            )
            # Привязываем платеж к obhod-подписке (для аудита) и закрываем.
            payment.subscription_id = None
            payment.status = "succeeded"
            if not payment.paid_at:
                payment.paid_at = datetime.utcnow()
            _pmeta = dict(payment.payment_metadata or {}) if isinstance(payment.payment_metadata, dict) else {}
            _pmeta["obhod_package_applied"] = bool(applied)
            payment.payment_metadata = _pmeta
            await session.commit()
            # Ветка пакета не доходит до общего сброса кэшей ниже: профиль для
            # сайта (платежи, пакет обхода) сбрасываем здесь.
            try:
                from app.services.cache import invalidate_site_profile_cache
                await invalidate_site_profile_cache(telegram_user_id)
            except Exception as _e:
                logger.debug(f"[{trace_id}] invalidate_site_profile_cache soft-fail: {_e}")
            if applied:
                try:
                    await bot.send_message(
                        chat_id=telegram_user_id,
                        text=(
                            "✅ <b>Пакет обхода подключен</b>\n\n"
                            "Лимит обхода поднят. Открыть ссылку обхода можно на "
                            "экране «Подключиться»."
                        ),
                        parse_mode="HTML",
                    )
                except Exception as _e:
                    logger.debug(f"[{trace_id}] obhod package notify soft-fail: {_e}")
            else:
                logger.error(
                    f"[{trace_id}] obhod package paid but NOT applied "
                    f"(нет активного обхода?): tg_id={telegram_user_id} package={plan_code}"
                )
                # 04 M9: деньги взяты, пакет не применен — раньше только лог.
                # Алерт админам и честное сообщение юзеру (один раз на платеж).
                if not _pmeta.get("obhod_package_alerted"):
                    from html import escape as _he
                    _alerted = False
                    for admin_id in (settings.ADMINS or []):
                        try:
                            await bot.send_message(
                                chat_id=admin_id,
                                text=(
                                    "⚠️ <b>Пакет обхода оплачен, но НЕ применен</b>\n\n"
                                    f"Telegram ID: <code>{telegram_user_id}</code>\n"
                                    f"Пакет: {_he(str(plan_code))}\n"
                                    f"Payment: <code>{_he(str(payment.external_id))}</code>\n\n"
                                    "Скорее всего нет активного обхода (Pro истек). "
                                    "Примените кап вручную или оформите возврат."
                                ),
                                parse_mode="HTML",
                            )
                            _alerted = True
                        except Exception as _ae:
                            logger.warning(f"[{trace_id}] obhod package alert to {admin_id} failed: {_ae}")
                    try:
                        await bot.send_message(
                            chat_id=telegram_user_id,
                            text=(
                                "⏳ <b>Оплата пакета обхода получена</b>\n\n"
                                "Автоматически применить пакет не получилось. "
                                "Администратор применит его вручную и свяжется с вами."
                            ),
                            parse_mode="HTML",
                        )
                    except Exception as _ue:
                        logger.debug(f"[{trace_id}] obhod package user notice failed: {_ue}")
                    if _alerted:
                        _pmeta["obhod_package_alerted"] = True
                        payment.payment_metadata = dict(_pmeta)
                        await session.commit()
            return

        # Если не нашли в metadata, определяем тариф и период по сумме платежа.
        # ВНИМАНИЕ: после ввода тарифов lite/standard/pro суммы пересекаются
        # (249, 1199, 2199 — двусмысленны). Этот fallback ОСОЗНАННО мапит
        # амбивалентные суммы в legacy basic/premium, чтобы новые юзеры
        # с разбитой metadata получили рабочую подписку (с меньшим набором
        # серверов, поправляется руками админа). Сам факт срабатывания —
        # ERROR, требует разбора почему metadata пустая.
        if not plan_code or not period_months:
            # Базовый: 99 (1 мес), 249 (3 мес), 499 (6 мес), 899 (12 мес)
            # Премиум: 199 (1 мес), 549 (3 мес), 999 (6 мес), 1799 (12 мес)
            # Порядок: от большего к меньшему, без перекрытий диапазонов
            logger.error(
                f"[{trace_id}] plan determined by AMOUNT FALLBACK — metadata broken. "
                f"amount={amount} payment_id={payment_id} tg_id={telegram_user_id}. "
                f"Юзер получит legacy basic/premium; для new-cohort юзера "
                f"подписку нужно вручную перепровизионить в нужный squad."
            )
            if amount >= 1799:
                plan_code = "premium"
                period_months = 12
            elif amount >= 999:
                plan_code = "premium"
                period_months = 6
            elif amount >= 899:       # 899 <= amount < 999
                plan_code = "basic"
                period_months = 12
            elif amount >= 549:       # 549 <= amount < 899
                plan_code = "premium"
                period_months = 3
            elif amount >= 499:       # 499 <= amount < 549
                plan_code = "basic"
                period_months = 6
            elif amount >= 249:       # 249 <= amount < 499
                plan_code = "basic"
                period_months = 3
            elif amount >= 199:       # 199 <= amount < 249
                plan_code = "premium"
                period_months = 1
            elif amount >= 99:        # 99 <= amount < 199
                plan_code = "basic"
                period_months = 1
            else:
                plan_code = "basic"
                period_months = 1
        
        # Хотфикс 2.1: оплаченная сумма обязана совпасть с прайсом. Иначе не
        # провижиним (никаких «Pro на год за 1 ₽»), платеж уходит на ручную проверку.
        _reason = _price_mismatch_reason(
            plan_code, period_months, amount, payment.currency, meta
        )
        if _reason and not (isinstance(meta, dict) and meta.get("review_approved")):
            await _hold_payment_for_review(
                session, payment, telegram_user_id, _reason, bot, trace_id
            )
            return "review"

        # 3.0: ядро выдачи вынесено в provision_paid_period (общий код с
        # LegacyProvisioningService.grant).
        review_approved = bool(isinstance(meta, dict) and meta.get("review_approved"))
        try:
            _res = await provision_paid_period(
                session,
                payment=payment,
                telegram_user_id=telegram_user_id,
                plan_code=plan_code,
                period_months=period_months,
                review_approved=review_approved,
                trace_id=trace_id,
            )
        except PaymentUserMissingError:
            return
        except RemnaUserDisabledError:
            await _hold_payment_for_review(
                session, payment, telegram_user_id, DISABLED_USER_REVIEW_REASON, bot, trace_id
            )
            return "review"
        subscription = _res.subscription
        telegram_user = _res.telegram_user
        plan_name = _res.plan_name
        valid_until = _res.valid_until
        actual_expire_at = _res.actual_expire_at

        # Re-lock payment для атомарной проверки/установки notified (гонка при повторных webhook)
        pay_lock = await session.execute(
            select(PaymentModel).where(PaymentModel.id == payment_id).with_for_update()
        )
        payment = pay_lock.scalar_one_or_none()
        if not payment:
            return
        meta = payment.payment_metadata or {}
        if not isinstance(meta, dict):
            meta = {}
        user_already_notified = bool(meta.get("notified"))
        admin_already_notified = bool(meta.get("admin_notified"))

        # H-2: единое значение даты истечения для user и admin сообщений.
        # actual_expire_at — реальная дата из Remnawave (учитывает продление существующей подписки),
        # valid_until — локально вычисленная.
        _display_expire = actual_expire_at if actual_expire_at else valid_until

        # --- 1. Уведомление пользователю (только если ещё не уведомляли) ---
        if user_already_notified:
            logger.info(f"[{trace_id}] user already notified: payment_id={payment_id}, skip user send")
        else:
            message_text = (
                "✅ <b>Оплата подтверждена, подписка активирована!</b>\n\n"
                f"💳 <b>Тариф:</b> {plan_name}\n"
                f"📅 <b>Действует до:</b> {_display_expire.strftime('%d.%m.%Y %H:%M')}\n"
                f"💰 <b>Сумма:</b> {amount:.2f}₽\n\n"
                "🎉 Теперь вы можете получить ссылку для настройки VPN."
            )

            from app.keyboards import get_subscription_link_keyboard
            try:
                await bot.send_message(
                    chat_id=telegram_user_id,
                    text=message_text,
                    reply_markup=get_subscription_link_keyboard(),
                    parse_mode="HTML"
                )
                meta["notified"] = True
            except Exception as user_err:
                logger.warning(f"[{trace_id}] не удалось отправить уведомление пользователю {telegram_user_id}: {user_err}")

        # --- 2. Уведомление администраторам (отдельный guard, ретраится независимо от notified) ---
        from html import escape as _he
        from app.config import settings as _settings
        if _settings.ADMINS and not admin_already_notified:
            _first = (telegram_user.first_name or "").strip()
            _last = (telegram_user.last_name or "").strip()
            user_full_name = f"{_first} {_last}".strip() or "Без имени"

            username_line = (
                f"🔗 @{_he(telegram_user.username)}\n" if telegram_user.username else ""
            )

            # Статистика по клиенту: какая это по счёту успешная оплата и сколько
            # всего заработано. Считаем ТОЛЬКО succeeded в RUB. Текущий платёж уже
            # закоммичен со status='succeeded' выше, поэтому он входит в счёт —
            # т.е. payment_number это порядковый номер именно этой оплаты.
            stats_row = await session.execute(
                select(
                    func.count(PaymentModel.id),
                    func.coalesce(func.sum(PaymentModel.amount), 0),
                ).where(
                    PaymentModel.telegram_user_id == telegram_user_id,
                    PaymentModel.status == "succeeded",
                    func.upper(PaymentModel.currency) == "RUB",
                )
            )
            payment_number, total_earned = stats_row.one()
            payment_number = int(payment_number or 0) or 1

            total_str = f"{float(total_earned or 0):.2f}".rstrip("0").rstrip(".")
            if "." not in total_str:
                total_str = f"{int(float(total_earned or 0))}"

            plan_label = _he(plan_name)
            if period_months:
                months_word = (
                    "месяц" if period_months == 1
                    else "месяца" if period_months in (2, 3, 4)
                    else "месяцев"
                )
                plan_label = f"{plan_label} {period_months} {months_word}"

            # Используем единую дату _display_expire (учитывает реальный expireAt из Remnawave при продлении)
            expires_str = _display_expire.strftime("%d.%m.%Y")

            currency_symbol = payment.currency.upper() if payment and payment.currency else "RUB"
            # external_id — это p.id из YooKassa (UUID формата xxxxxxxx-xxxx-...)
            external_id = payment.external_id if payment else "—"
            remna_id = subscription.remna_user_id or "—"

            # Сумма: показываем копейки только если они не нулевые
            amount_str = f"{amount:.2f}".rstrip("0").rstrip(".")
            if "." not in amount_str:
                amount_str = f"{int(amount)}"

            # Цветовой акцент + порядковый номер оплаты по этому клиенту.
            if payment_number <= 1:
                count_line = "🟢 Новый клиент · 1-я оплата"
            else:
                count_line = f"🔁 Постоянный клиент · {payment_number}-я оплата"

            admin_text = (
                "💰 <b>Новая оплата VPN</b>\n\n"
                f"👤 <b>{_he(user_full_name)}</b>\n"
                f"{username_line}"
                f"🆔 ID: <code>{telegram_user_id}</code>\n\n"
                "<blockquote>"
                f"Тариф: {plan_label}\n"
                f"Сумма: {amount_str} {_he(currency_symbol)}"
                "</blockquote>\n\n"
                "<blockquote>"
                f"{count_line}\n"
                f"📈 Всего с клиента: {total_str} ₽"
                "</blockquote>\n\n"
                f"📅 Действует до: {expires_str}\n\n"
                f"Payment ID: <code>{_he(str(external_id))}</code>\n"
                f"Remnawave ID: <code>{_he(str(remna_id))}</code>"
            )

            admin_notified = False
            for admin_id in _settings.ADMINS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=admin_text,
                        parse_mode="HTML"
                    )
                    admin_notified = True
                except Exception as admin_err:
                    logger.warning(f"[{trace_id}] не удалось отправить уведомление админу {admin_id}: {admin_err}")

            # Ставим admin_notified только если хотя бы одному админу дошло.
            # Иначе оставляем False — recovery-loop сможет повторить.
            if admin_notified:
                meta["admin_notified"] = True
        elif admin_already_notified:
            logger.info(f"[{trace_id}] admins already notified: payment_id={payment_id}, skip admin send")

        payment.payment_metadata = meta
        await session.commit()
        
        logger.info(
            f"[{trace_id}] subscription_provisioning_success: payment_id={payment_id} "
            f"subscription_id={subscription.id} plan={plan_code} period={period_months}m "
            f"tg_id={telegram_user_id} remna_user_id={subscription.remna_user_id}"
        )

        # Реферальный трекер /sun718: если плательщик — приглашённый юзер,
        # шлёт админу алерты B (+N мес) и C (бонус-порог). Soft-fail внутри.
        try:
            from app.services.referral_tracker import notify_referral_payment_if_applicable
            await notify_referral_payment_if_applicable(bot, session, payment)
        except Exception as _ref_e:
            logger.warning(f"[{trace_id}] referral_tracker hook soft-fail: {_ref_e}")

    except ProvisioningPendingError as ppe:
        # Уже залогировано и помечено в _mark_provisioning_failed.
        # Пробрасываем дальше — webhook вернет 503, юзер будет уведомлен только когда
        # reconciler / повторный webhook доведут sync до конца.
        # Хотфикс 2.1: деньги взяты, доступа нет — админ должен узнать сразу
        # (один алерт на платеж за 6 часов, дальше ретраи молча).
        await _alert_paid_not_provisioned(bot, payment_id, telegram_user_id, str(ppe), trace_id)
        raise
    except Exception as e:
        logger.error(f"[{trace_id}] handle_successful_payment failed: payment_id={payment_id} user={telegram_user_id} err={e}")
        import traceback
        logger.debug(traceback.format_exc())
        try:
            payment_result = await session.execute(
                select(PaymentModel).where(PaymentModel.id == payment_id)
            )
            payment = payment_result.scalar_one_or_none()
            if payment:
                meta = payment.payment_metadata or {}
                if not isinstance(meta, dict):
                    meta = dict(meta) if meta else {}
                meta["needs_provisioning"] = True
                meta["provisioning_attempted_at"] = datetime.utcnow().isoformat()
                meta["provisioning_error"] = str(e)[:500]
                payment.payment_metadata = meta
                await session.commit()
                logger.info(f"[{trace_id}] payment needs_provisioning set: payment_id={payment_id}")
        except Exception as e2:
            logger.error(f"[{trace_id}] failed to set needs_provisioning: {e2}")


async def check_payment_status(payment_id: str) -> Optional[Dict[str, Any]]:
    """Статус платежа в YooKassa (3.0: async gateway вместо SDK).

    Returns: dict с status/amount/currency/description/metadata/paid/refunded_amount/
    payment_method/card_fingerprint, {"error": "not_found"} если платеж не найден,
    или None при ошибке API/сети/настроек.
    """
    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_API_KEY:
        logger.error("YOOKASSA_SHOP_ID и YOOKASSA_API_KEY должны быть настроены")
        return None
    from app.infra.yookassa import default_gateway

    return await default_gateway().get_payment(payment_id)


async def _compute_extend_expire_str(client: RemnaClient, remna_user_id: str, period_months: int) -> str:
    """Compute new expireAt by extending from current Remnawave expireAt (or now if expired/unavailable)."""
    now_utc = datetime.now(timezone.utc)
    base = now_utc
    try:
        remna_data = await client.get_user_by_id(remna_user_id)
        raw = remna_data.get("response", remna_data) if isinstance(remna_data, dict) else {}
        if not isinstance(raw, dict):
            raw = {}
        expire_raw = raw.get("expireAt") or raw.get("expires_at") or raw.get("valid_until")
        if expire_raw:
            expire_str = str(expire_raw).replace("Z", "+00:00")
            current_exp = datetime.fromisoformat(expire_str)
            if current_exp.tzinfo is None:
                current_exp = current_exp.replace(tzinfo=timezone.utc)
            else:
                current_exp = current_exp.astimezone(timezone.utc)
            if current_exp > now_utc:
                base = current_exp
                logger.debug(f"remna expire_at extended from current: {current_exp.isoformat()}")
    except Exception as fetch_e:
        logger.debug(f"Could not fetch current expireAt for {remna_user_id}: {fetch_e}")
    new_expire = base + relativedelta(months=period_months)
    return new_expire.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_remna_user_gone_error(exc: Exception) -> bool:
    """404 (юзер удален) или 400 (id не принят панелью, legacy-UUID в 3.x)."""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return exc.response.status_code in (400, 404)
    return False


async def _read_stored_remna_user(client, remna_user_id: str) -> Optional[Dict[str, Any]]:
    """GET сохраненного юзера. None — юзера больше нет (или id legacy-формата);
    любая другая ошибка пробрасывается (панель недоступна != юзера нет)."""
    if not str(remna_user_id).strip().isdigit():
        return None
    try:
        data = await client.get_user_by_id(str(remna_user_id))
    except Exception as e:
        if _is_remna_user_gone_error(e):
            return None
        raise
    raw = data.get("response", data) if isinstance(data, dict) else None
    if not isinstance(raw, dict) or not raw:
        # пустой ответ — не доказательство, что юзера нет: не создаем дубль
        raise RuntimeError(f"remna user {remna_user_id}: empty response")
    return data


async def get_or_create_remna_user_and_get_subscription_url(
    telegram_user_id: int,
    subscription_id: int,
    period_months: Optional[int] = None,
    enable_if_disabled: bool = False,
) -> Optional[str]:
    """
    period_months: если передан — продлевает expireAt в Remna от текущего
    expireAt (как provision_tariff). Если None — fallback на subscription.valid_until.
    Ревью N2: выдача по оплате (period_months передан) для юзера, отключенного
    в панели вручную, бросает RemnaUserDisabledError и ничего не пишет;
    enable_if_disabled=True (оплата одобрена админом) включает такого юзера.
    Resync (period_months=None) статус не трогает, как и раньше.
    """
    """Получает или создает пользователя в Remna API и возвращает subscription URL"""
    try:
        if not SessionLocal:
            return None
        
        async with SessionLocal() as session:
            user_result = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
            )
            telegram_user = user_result.scalar_one_or_none()
            
            if not telegram_user:
                logger.error(f"Пользователь {telegram_user_id} не найден")
                return None
            
            sub_result = await session.execute(
                select(Subscription).where(Subscription.id == subscription_id)
            )
            subscription = sub_result.scalar_one_or_none()
            
            if not subscription:
                logger.error(f"Подписка {subscription_id} не найдена")
                return None
            
            client = RemnaClient()
            
            try:
                stored_user_data = None
                if telegram_user.remna_user_id:
                    # Ревью B1: сохраненный id может указывать на удаленного в
                    # панели юзера или на legacy-UUID, который 3.x не принимает.
                    # Как на проде: такой id не блокирует выдачу — забываем его
                    # и идем в поиск по telegramId / создание. Ошибки панели
                    # (5xx, таймаут) по-прежнему фатальны: это «не знаю», а не
                    # «юзера нет».
                    stored_user_data = await _read_stored_remna_user(
                        client, str(telegram_user.remna_user_id)
                    )
                    if stored_user_data is None:
                        stale_id = str(telegram_user.remna_user_id)
                        logger.warning(
                            f"remna_user_id={stale_id} for tg_id={telegram_user_id} is gone in "
                            f"Remnawave (404/400/legacy id) — clearing it, falling back to "
                            f"telegramId lookup/create"
                        )
                        telegram_user.remna_user_id = None
                        if subscription.remna_user_id and str(subscription.remna_user_id) == stale_id:
                            subscription.remna_user_id = None
                        await session.commit()

                if telegram_user.remna_user_id:
                    # Если пользователь уже существует, обновляем expireAt и сквад
                    remna_user_id = str(telegram_user.remna_user_id)

                    # КРИТИЧНО: expireAt + сквад тарифа + лимит устройств одним PATCH
                    # по политике services/remna_tariff (ручные сквады и поднятые
                    # лимиты не затираются). Любая ошибка -> RemnaTariffError ->
                    # выдача не засчитывается (provisioning failed, retry).
                    # Если period_months передан — цель берется из Phase A
                    # (idempotent target) или считается от текущего expireAt.
                    # Иначе — fallback на subscription.valid_until (resync).
                    new_expire_str = None
                    if period_months is not None:
                        # ИДЕМПОТЕНТНОСТЬ: если уже есть зафиксированный target из
                        # Phase A — используем его, не пересчитываем от текущего expireAt.
                        # Иначе при retry _compute_extend_expire_str будет каждый раз
                        # добавлять +period к уже сохраненному в Remnawave значению —
                        # подписка уезжает в годы вперед (был такой баг).
                        if subscription.remnawave_expected_expire_at:
                            new_expire_str = normalize_expire_at(subscription.remnawave_expected_expire_at)
                        else:
                            new_expire_str = await _compute_extend_expire_str(client, remna_user_id, period_months)
                    elif subscription.valid_until:
                        new_expire_str = normalize_expire_at(subscription.valid_until)
                    logger.info(
                        f"remna tariff apply: remna_user_id={remna_user_id} plan={subscription.plan_code} "
                        f"new_expire={new_expire_str}"
                    )
                    from app.services.remna_tariff import apply_tariff_to_remna_user
                    await apply_tariff_to_remna_user(
                        client, remna_user_id, subscription.plan_code, expire_at=new_expire_str,
                        user_data=stored_user_data,
                        enable_if_disabled=enable_if_disabled,
                        refuse_if_disabled=period_months is not None,
                    )

                    subscription_url = await client.get_user_subscription_url(telegram_user.remna_user_id)
                    if subscription_url:
                        # Закрываем клиент перед возвратом
                        await client.close()
                        return subscription_url
                
                # remna_user_id не сохранен в БД — ищем по telegram_id в Remnawave
                # (пользователь мог быть создан ранее без привязки UUID к telegram_users)
                # strict: ошибка панели != «юзера нет». Раньше любая ошибка
                # превращалась в None и мы создавали второго юзера с тем же
                # telegramId. Теперь выдача падает и ретраится.
                try:
                    found_remna = await client.get_user_by_telegram_id(telegram_user_id, strict=True)
                except Exception as lookup_e:
                    logger.error(
                        f"remna lookup by telegram_id failed, not creating a duplicate: "
                        f"tg_id={telegram_user_id} err={lookup_e}"
                    )
                    return None

                if found_remna:
                    remna_user_id = found_remna.uuid
                    logger.info(f"remna user found by telegram_id={telegram_user_id}: remna_user_id={remna_user_id}")
                    # Upsert RemnaUser into local DB — required by FK on telegram_users.remna_user_id
                    await session.execute(
                        pg_insert(RemnaUser).values(
                            remna_id=remna_user_id,
                            username=getattr(found_remna, 'username', None),
                        ).on_conflict_do_nothing(index_elements=['remna_id'])
                    )
                    telegram_user.remna_user_id = remna_user_id
                    await session.commit()
                    # expireAt + сквад + лимит одним PATCH (политика remna_tariff)
                    _new_expire = None
                    if period_months is not None:
                        # Та же идемпотентность что и в основной ветке: предпочитаем
                        # зафиксированный target из Phase A, чтобы retry не дрейфил.
                        if subscription.remnawave_expected_expire_at:
                            _new_expire = normalize_expire_at(subscription.remnawave_expected_expire_at)
                        else:
                            _new_expire = await _compute_extend_expire_str(client, remna_user_id, period_months)
                    elif subscription.valid_until:
                        _new_expire = normalize_expire_at(subscription.valid_until)
                    from app.services.remna_tariff import apply_tariff_to_remna_user
                    await apply_tariff_to_remna_user(
                        client, remna_user_id, subscription.plan_code, expire_at=_new_expire,
                        enable_if_disabled=enable_if_disabled,
                        refuse_if_disabled=period_months is not None,
                    )
                    # Получаем subscription URL и сохраняем
                    subscription_url = await client.get_user_subscription_url(remna_user_id)
                    subscription.remna_user_id = remna_user_id
                    if subscription_url:
                        if not subscription.config_data:
                            subscription.config_data = {}
                        subscription.config_data["subscription_url"] = subscription_url
                    await session.commit()
                    await client.close()
                    return subscription_url

                # Формируем username по единой логике
                from app.utils.remna_username import build_remna_username
                username = build_remna_username(
                    telegram_id=telegram_user_id,
                    username=telegram_user.username,
                    first_name=telegram_user.first_name,
                    last_name=telegram_user.last_name,
                )
                # Генерируем пароль, соответствующий требованиям Remna API
                password = generate_remna_password(length=24)
                
                # Дата истечения: используем relativedelta для точных календарных месяцев (как provision_tariff)
                # Предпочитаем зафиксированную в Phase A цель (идемпотентно для retry).
                if subscription.remnawave_expected_expire_at:
                    expire_at = subscription.remnawave_expected_expire_at
                elif period_months is not None and period_months > 0:
                    from datetime import timezone as _tz
                    from dateutil.relativedelta import relativedelta as _rd
                    expire_at = datetime.now(_tz.utc) + _rd(months=period_months)
                else:
                    expire_at = subscription.valid_until
                
                # Получаем сквад для плана подписки перед созданием пользователя
                squad_name = await get_squad_name_for_plan(subscription.plan_code)
                squad_uuid = None
                if squad_name:
                    try:
                        squad = await client.get_squad_by_name(squad_name)
                        if squad:
                            squad_uuid = squad.get('uuid')
                            logger.debug(f"remna squad resolved: name={squad_name} uuid={squad_uuid}")
                    except Exception as squad_e:
                        logger.warning(f"remna squad lookup failed: name={squad_name} err={squad_e}")

                new_device_limit = _device_limit_for_plan(subscription.plan_code)
                logger.info(f"remna user create: tg_id={telegram_user_id} username={username} expire_at={expire_at} device_limit={new_device_limit}")
                # Хотфикс 2.1: при занятом username чужого юзера НЕ забираем (раньше
                # тут искали по username и продлевали/перетарифицировали чужой
                # аккаунт, привязывая его к плательщику). create_user_unique
                # берет другой username; «свой» юзер возможен только при совпадении
                # telegramId или сохраненного у нас id.
                adopted = False
                try:
                    remna_user_data, adopted = await client.create_user_unique(
                        telegram_id=telegram_user_id,
                        base_username=username,
                        known_remna_id=telegram_user.remna_user_id,
                        password=password,
                        expire_at=expire_at,
                        active_internal_squads=[squad_uuid] if squad_uuid else None,
                        hwid_device_limit=new_device_limit,
                    )
                    logger.debug(f"remna user created: tg_id={telegram_user_id} adopted={adopted}")
                except Exception as e:
                    logger.error(f"Ошибка при создании пользователя в Remna API: tg_id={telegram_user_id} err={e}")
                    return None
                if isinstance(remna_user_data, dict) and remna_user_data.get("username"):
                    username = remna_user_data["username"]

                remna_user_id = None
                if isinstance(remna_user_data, dict):
                    # Пробуем разные варианты получения UUID
                    # Структура ответа от /api/users может быть разной
                    remna_user_id = (
                        remna_user_data.get("uuid") or 
                        remna_user_data.get("id") or 
                        remna_user_data.get("_id") or
                        remna_user_data.get("userId")
                    )
                    
                    # Если не нашли в корне, проверяем вложенные структуры
                    if not remna_user_id:
                        if "response" in remna_user_data:
                            response = remna_user_data["response"]
                            # Может быть объект пользователя или список
                            if isinstance(response, dict):
                                remna_user_id = (
                                    response.get("uuid") or 
                                    response.get("id") or 
                                    response.get("_id") or
                                    response.get("userId")
                                )
                            elif isinstance(response, list) and len(response) > 0:
                                # Если ответ - список, берем первый элемент
                                first_user = response[0]
                                remna_user_id = (
                                    first_user.get("uuid") or 
                                    first_user.get("id") or 
                                    first_user.get("_id") or
                                    first_user.get("userId")
                                )
                        if not remna_user_id and "data" in remna_user_data:
                            data = remna_user_data["data"]
                            remna_user_id = (
                                data.get("uuid") or 
                                data.get("id") or 
                                data.get("_id") or
                                data.get("userId")
                            )
                
                if not remna_user_id:
                    logger.error(f"Не удалось получить ID пользователя из Remna API. Ответ: {remna_user_data}")
                    return None
                
                logger.info(f"Пользователь создан в Remna API: remna_user_id={remna_user_id}")
                
                # Извлекаем полные данные пользователя из ответа
                user_response_data = None
                if isinstance(remna_user_data, dict):
                    if "response" in remna_user_data and isinstance(remna_user_data["response"], dict):
                        user_response_data = remna_user_data["response"]
                    else:
                        user_response_data = remna_user_data
                
                # Получаем subscription URL из ответа (он уже есть в response)
                subscription_url = None
                if user_response_data:
                    subscription_url = (
                        user_response_data.get("subscriptionUrl") or 
                        user_response_data.get("subscription_url")
                    )
                    if not subscription_url and "subscriptionToken" in user_response_data:
                        token = user_response_data.get("subscriptionToken") or user_response_data.get("subscription_token")
                        if token:
                            _sub_base2 = str(settings.SUBSCRIPTION_BASE_URL).rstrip("/") if settings.SUBSCRIPTION_BASE_URL else "https://sub.crs-projects.com"
                            subscription_url = f"{_sub_base2}/{token}"
                
                from app.services.remna_service import safe_remna_raw

                # Создаем запись в remna_users перед обновлением telegram_users (для Foreign Key)
                remna_user_result = await session.execute(
                    select(RemnaUser).where(RemnaUser.remna_id == str(remna_user_id))
                )
                remna_user = remna_user_result.scalar_one_or_none()
                
                if not remna_user:
                    # Создаем новую запись в remna_users
                    remna_user = RemnaUser(
                        remna_id=str(remna_user_id),
                        username=username,
                        email=user_response_data.get("email") if user_response_data else None,
                        raw_data=safe_remna_raw(user_response_data if user_response_data else remna_user_data)
                    )
                    session.add(remna_user)
                    logger.info(f"Создана запись в remna_users для remna_id={remna_user_id}")
                else:
                    # Обновляем существующую запись
                    remna_user.username = username
                    remna_user.raw_data = safe_remna_raw(user_response_data if user_response_data else remna_user_data)
                    remna_user.last_synced_at = datetime.utcnow()
                    logger.info(f"Обновлена запись в remna_users для remna_id={remna_user_id}")
                
                # Теперь обновляем telegram_user и subscription
                telegram_user.remna_user_id = str(remna_user_id)
                subscription.remna_user_id = str(remna_user_id)
                
                # Сохраняем subscription URL в config_data подписки
                if subscription_url and subscription_url.strip():
                    if not subscription.config_data:
                        subscription.config_data = {}
                    subscription.config_data["subscription_url"] = subscription_url.strip()
                    logger.info("✅ Subscription URL сохранен в config_data")
                else:
                    logger.warning("⚠️ Subscription URL не найден в ответе создания пользователя")
                
                await session.commit()
                
                # Если subscription URL не был в ответе, пытаемся получить его отдельным запросом
                if not subscription_url or not subscription_url.strip():
                    logger.info(f"📥 Получение subscription URL отдельным запросом для remna_user_id={remna_user_id}")
                    subscription_url = await client.get_user_subscription_url(str(remna_user_id))
                    if subscription_url and subscription_url.strip():
                        # Обновляем config_data с полученной ссылкой
                        if not subscription.config_data:
                            subscription.config_data = {}
                        subscription.config_data["subscription_url"] = subscription_url.strip()
                        await session.commit()
                        logger.info("✅ Subscription URL получен и сохранен")
                    else:
                        logger.error(f"❌ Не удалось получить subscription URL для remna_user_id={remna_user_id}")
                
                # Досыпаем сквад тарифа / лимит по общей политике (если при create
                # сквад не нашелся — здесь будет RemnaTariffError и выдача не
                # засчитается, а не «оплачено, но без нод»). expireAt уже задан в create.
                from app.services.remna_tariff import apply_tariff_to_remna_user
                await apply_tariff_to_remna_user(
                    client, str(remna_user_id), subscription.plan_code,
                    # свой юзер после сбоя: create не выполнялся, дату ставим здесь
                    expire_at=normalize_expire_at(expire_at) if adopted else None,
                    enable_if_disabled=enable_if_disabled,
                    refuse_if_disabled=period_months is not None,
                )

                return subscription_url
                
            finally:
                await client.close()
                
    except RemnaUserDisabledError:
        raise  # ревью N2: решение за админом, вызывающий ставит платеж на ревью
    except Exception as e:
        logger.error(f"Ошибка при получении subscription URL: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return None