import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from dateutil.relativedelta import relativedelta
import secrets
import string
import uuid
from sqlalchemy.exc import IntegrityError
from yookassa import Configuration, Payment
from yookassa.domain.notification import WebhookNotification
from app.config import settings
from app.logger import logger
from app.db.session import SessionLocal
from app.db.models import Payment as PaymentModel, Subscription, TelegramUser, RemnaUser
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.remnawave.client import RemnaClient, normalize_expire_at

Configuration.account_id = settings.YOOKASSA_SHOP_ID
Configuration.secret_key = settings.YOOKASSA_API_KEY


async def get_squad_name_for_plan(plan_code: Optional[str]) -> Optional[str]:
    """Возвращает имя сквада для плана подписки"""
    if not plan_code:
        return None
    
    plan_code_lower = plan_code.lower()
    if plan_code_lower == 'basic':
        return 'basic'  # Исправлено: было 'base', должно быть 'basic'
    elif plan_code_lower == 'premium':
        return 'premium'
    elif plan_code_lower == 'trial':
        # Пробный период использует базовый сквад
        return 'basic'  # Исправлено: было 'base', должно быть 'basic'
    
    return None


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
}


async def create_payment(
    amount_rub: int,
    description: str,
    user_id: int,
    plan_code: Optional[str] = None,
    period_months: Optional[int] = None,
    request_id: Optional[int] = None,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> tuple[str, str]:
    """Создает платеж в YooKassa и возвращает (payment_url, external_id)"""
    trace_id = str(uuid.uuid4())
    try:
        if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_API_KEY:
            raise ValueError("YOOKASSA_SHOP_ID и YOOKASSA_API_KEY должны быть настроены")
        
        api_key = str(settings.YOOKASSA_API_KEY).strip()
        if not api_key:
            raise ValueError("YOOKASSA_API_KEY пустой")
        
        shop_id = str(settings.YOOKASSA_SHOP_ID).strip()
        Configuration.account_id = shop_id
        Configuration.secret_key = api_key
        
        if not settings.YOOKASSA_RETURN_URL:
            raise ValueError("YOOKASSA_RETURN_URL должен быть настроен")
        
        metadata = {"tg_user_id": user_id}
        if plan_code:
            metadata["plan_code"] = plan_code
        if period_months:
            metadata["period_months"] = str(period_months)
        if request_id:
            metadata["request_id"] = str(request_id)
        
        payment_data = {
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": str(settings.YOOKASSA_RETURN_URL)},
            "description": description,
            "metadata": metadata,
            "payment_method_types": ["bank_card", "sbp", "yoo_money"]
        }
        
        # Детерминированный idempotence_key: user+план+сумма+10-мин. окно.
        # Повторный вызов с теми же параметрами в течение 10 минут вернёт тот же платёж YooKassa.
        _time_bucket = int(datetime.utcnow().timestamp()) // 600
        idempotence_key = f"cp_{user_id}_{plan_code or 'x'}_{period_months or 0}_{amount_rub}_{_time_bucket}"

        logger.info(
            f"[{trace_id}] calling YooKassa Payment.create: user={user_id} amount={amount_rub} "
            f"plan={plan_code} period={period_months} idempotence_key={idempotence_key}"
        )
        try:
            p = Payment.create(payment_data, idempotence_key)
            payment_id = p.id
            payment_url = p.confirmation.confirmation_url
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
            "yookassa_response": p.dict() if hasattr(p, "dict") else str(p),
            "trace_id": trace_id,
            "plan_code": plan_code,
            "period_months": period_months,
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
                            status=p.status or "pending",
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
    """Обрабатывает webhook от YooKassa"""
    trace_id = str(uuid.uuid4())
    try:
        if not webhook_data:
            logger.error(f"[{trace_id}] webhook received: empty body")
            return False
        
        event = webhook_data.get("event")
        if not event:
            logger.error(f"[{trace_id}] webhook received: missing event")
            return False
        
        logger.info(f"[{trace_id}] webhook received: event={event}")
        
        notification = WebhookNotification(webhook_data)
        payment = notification.object
        
        if not payment:
            logger.error(f"[{trace_id}] webhook received: no payment object")
            return False
        
        payment_id = payment.id
        webhook_metadata = payment.metadata or {}
        description = payment.description

        # Webhook is a trigger only. Always verify payment status directly with YooKassa API.
        logger.info(f"[{trace_id}] webhook trigger: external_id={payment_id} — verifying via YooKassa API")
        api_data = await check_payment_status(payment_id)
        if not api_data:
            logger.error(f"[{trace_id}] API verification failed: external_id={payment_id}")
            return False
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
                        allowed = VALID_STATUS_TRANSITIONS.get("pending", set())
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
                if not payment_db.subscription_id:
                    await handle_successful_payment(
                        session=session,
                        payment_id=payment_db.id,
                        telegram_user_id=telegram_user_id,
                        amount=amount,
                        description=description or "CRS VPN 30 дней",
                        bot=bot,
                        trace_id=trace_id,
                    )
                else:
                    logger.info(
                        f"[{trace_id}] idempotent skip: payment already provisioned "
                        f"external_id={payment_id} subscription_id={payment_db.subscription_id}"
                    )
        
        return True
        
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


async def handle_successful_payment(
    session,
    payment_id: int,
    telegram_user_id: int,
    amount: float,
    description: str,
    bot,
    trace_id: Optional[str] = None,
    provision_for_verification: bool = False,
    custom_message: Optional[str] = None,
) -> None:
    """Обрабатывает успешный платеж: создает подписку и отправляет пользователю ссылку.
    provision_for_verification=True: оставляет status=pending, отправляет custom_message (для crypto).
    """
    trace_id = trace_id or str(uuid.uuid4())
    # NOTE: cache invalidation перенесён ПОСЛЕ commit (см. ниже)

    try:
        payment_result = await session.execute(
            select(PaymentModel).where(PaymentModel.id == payment_id)
        )
        payment = payment_result.scalar_one_or_none()
        if not payment:
            logger.error(f"[{trace_id}] handle_successful_payment: payment not found id={payment_id}")
            return

        meta = payment.payment_metadata or {}
        if isinstance(meta, dict) and meta.get("notified"):
            # Verify that Remnawave was actually provisioned; if remna_user_id is still NULL,
            # provisioning silently failed despite notification — allow re-run
            _tg_notif_r = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
            )
            _tg_notif = _tg_notif_r.scalar_one_or_none()
            if _tg_notif and _tg_notif.remna_user_id:
                logger.info(f"[{trace_id}] handle_successful_payment: already notified and provisioned payment_id={payment_id}")
                return
            logger.warning(
                f"[{trace_id}] handle_successful_payment: notified but remna_user_id missing — "
                f"re-running provisioning payment_id={payment_id} tg_id={telegram_user_id}"
            )

        # Идемпотентность provisioning: re-check subscription_id под блокировкой.
        # Защищает от race condition при конкурентных вызовах (webhook + recovery, двойной клик).
        pay_locked = await session.execute(
            select(PaymentModel).where(PaymentModel.id == payment_id).with_for_update()
        )
        payment_locked = pay_locked.scalar_one_or_none()
        if not payment_locked:
            logger.error(f"[{trace_id}] handle_successful_payment: payment lost after lock id={payment_id}")
            return
        if payment_locked.subscription_id:
            meta_locked = payment_locked.payment_metadata or {}
            needs_reprovision = isinstance(meta_locked, dict) and meta_locked.get("needs_provisioning")
            if not needs_reprovision:
                # Also check if Remnawave was actually provisioned (remna_user_id may be NULL)
                _tg_lock_r = await session.execute(
                    select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
                )
                _tg_lock = _tg_lock_r.scalar_one_or_none()
                if _tg_lock and _tg_lock.remna_user_id:
                    logger.info(
                        f"[{trace_id}] handle_successful_payment: already provisioned (concurrent call) "
                        f"payment_id={payment_id} subscription_id={payment_locked.subscription_id}"
                    )
                    return
                needs_reprovision = True  # remna_user_id NULL = provisioning silently failed
            logger.info(
                f"[{trace_id}] handle_successful_payment: re-provisioning after prior failure "
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

        # Если не нашли в metadata, определяем тариф и период по сумме платежа
        if not plan_code or not period_months:
            # Базовый: 99 (1 мес), 249 (3 мес), 499 (6 мес), 899 (12 мес)
            # Премиум: 199 (1 мес), 549 (3 мес), 999 (6 мес), 1799 (12 мес)
            # Порядок: от большего к меньшему, без перекрытий диапазонов
            logger.warning(
                f"[{trace_id}] plan determined by amount fallback "
                f"(metadata missing plan_code/period_months): amount={amount} payment_id={payment_id}"
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
        
        # Определяем plan_name
        if plan_code == "premium":
            plan_name = "Премиум"
        else:
            plan_name = "Базовый"
        
        # Используем calendar months (не 30-дневное приближение) для точного expireAt
        valid_until = datetime.utcnow() + relativedelta(months=period_months)
        
        user_result = await session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
        )
        telegram_user = user_result.scalar_one_or_none()
        
        if not telegram_user:
            logger.error(f"[{trace_id}] subscription_provisioning_failed: tg_id={telegram_user_id} not found in DB")
            return
        
        sub_result = await session.execute(
            select(Subscription).where(
                Subscription.telegram_user_id == telegram_user_id,
                Subscription.active == True
            )
        )
        existing_sub = sub_result.scalar_one_or_none()
        
        if existing_sub:
            existing_sub.plan_code = plan_code
            existing_sub.plan_name = plan_name
            existing_sub.valid_until = valid_until
            existing_sub.active = True
            subscription = existing_sub
            logger.info(f"[{trace_id}] subscription updated: tg_id={telegram_user_id} plan={plan_code} period={period_months}m")
        else:
            subscription = Subscription(
                telegram_user_id=telegram_user_id,
                plan_code=plan_code,
                plan_name=plan_name,
                active=True,
                valid_until=valid_until
            )
            session.add(subscription)
            logger.info(f"[{trace_id}] subscription created: tg_id={telegram_user_id} plan={plan_code} period={period_months}m")
        
        await session.commit()
        await session.refresh(subscription)
        
        payment_result = await session.execute(
            select(PaymentModel).where(PaymentModel.id == payment_id)
        )
        payment = payment_result.scalar_one_or_none()
        if payment:
            payment.subscription_id = subscription.id
            if not provision_for_verification:
                payment.status = "succeeded"
                payment.paid_at = datetime.utcnow()
            await session.commit()
        
        actual_expire_at: Optional[datetime] = None

        subscription_url = await get_or_create_remna_user_and_get_subscription_url(
            telegram_user_id=telegram_user_id,
            subscription_id=subscription.id,
            period_months=period_months,  # P1: продлеваем expireAt от текущего в Remnawave
        )

        if subscription_url:
            if not subscription.config_data:
                subscription.config_data = {}
            subscription.config_data["subscription_url"] = subscription_url
            await session.commit()

        # M-1: populate subscription.remna_user_id + H-2: fetch actual Remnawave expiry for message
        try:
            tg_refreshed_result = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
            )
            tg_refreshed = tg_refreshed_result.scalar_one_or_none()
            if tg_refreshed and tg_refreshed.remna_user_id:
                if not subscription.remna_user_id:
                    subscription.remna_user_id = tg_refreshed.remna_user_id
                    await session.commit()
                    logger.debug(f"[{trace_id}] subscription.remna_user_id set: {tg_refreshed.remna_user_id}")
                # H-2: fetch actual expiry from Remnawave
                try:
                    _rc = RemnaClient()
                    try:
                        remna_data = await _rc.get_user_by_id(str(tg_refreshed.remna_user_id))
                        _raw = remna_data.get("response", remna_data) if isinstance(remna_data, dict) else {}
                        if not isinstance(_raw, dict):
                            _raw = remna_data if isinstance(remna_data, dict) else {}
                        expire_raw = _raw.get("expireAt")
                        if expire_raw:
                            from app.tasks.expiry_notifier import _parse_expire_dt as _pdt
                            actual_expire_at = _pdt(expire_raw)
                            if actual_expire_at:
                                actual_expire_at = actual_expire_at.replace(tzinfo=None)
                    finally:
                        await _rc.close()
                except Exception as _remna_e:
                    logger.debug(f"[{trace_id}] H-2: could not fetch actual expiry from Remnawave: {_remna_e}")
        except Exception as _m1_e:
            logger.debug(f"[{trace_id}] M-1/H-2 post-provision update failed: {_m1_e}")

        # Safety net: if Remnawave provisioning silently failed (returned None),
        # mark needs_provisioning=True and abort — do NOT notify with a broken subscription
        if not subscription_url:
            _tg_chk_r = await session.execute(
                select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
            )
            _tg_chk = _tg_chk_r.scalar_one_or_none()
            if not (_tg_chk and _tg_chk.remna_user_id):
                _pay_r = await session.execute(
                    select(PaymentModel).where(PaymentModel.id == payment_id)
                )
                _pay = _pay_r.scalar_one_or_none()
                if _pay:
                    _pmeta = dict(_pay.payment_metadata or {})
                    _pmeta["needs_provisioning"] = True
                    _pmeta["provisioning_error"] = "remna_user_id not set after provisioning (silent failure)"
                    _pay.payment_metadata = _pmeta
                    await session.commit()
                logger.warning(
                    f"[{trace_id}] provisioning_silent_failure: remna_user_id not set after provisioning, "
                    f"marking needs_provisioning=True. payment_id={payment_id} tg_id={telegram_user_id}"
                )
                return  # Recovery loop will retry; don't notify with broken subscription

        # P2: cache invalidation ПОСЛЕ commit — чтобы пользователь не увидел старый статус
        try:
            from app.services.cache import invalidate_subscription_cache, invalidate_sync_cache
            await invalidate_subscription_cache(telegram_user_id)
            await invalidate_sync_cache(telegram_user_id)
            logger.debug(f"[{trace_id}] cache invalidated after commit")
        except Exception as cache_e:
            logger.warning(f"[{trace_id}] cache invalidation failed: {cache_e}")

        # Re-lock payment для атомарной проверки/установки notified (гонка при повторных webhook)
        pay_lock = await session.execute(
            select(PaymentModel).where(PaymentModel.id == payment_id).with_for_update()
        )
        payment = pay_lock.scalar_one_or_none()
        if not payment:
            return
        meta = payment.payment_metadata or {}
        if isinstance(meta, dict) and meta.get("notified"):
            logger.info(f"[{trace_id}] subscription notified already: payment_id={payment_id}, skip send")
            return

        if custom_message:
            message_text = custom_message
        else:
            # H-2: use actual Remnawave expiry if available, fallback to locally computed valid_until
            _display_expire = actual_expire_at if actual_expire_at else valid_until
            message_text = (
                "✅ <b>Оплата подтверждена, подписка активирована!</b>\n\n"
                f"💳 <b>Тариф:</b> {plan_name}\n"
                f"📅 <b>Действует до:</b> {_display_expire.strftime('%d.%m.%Y %H:%M')}\n"
                f"💰 <b>Сумма:</b> {amount:.2f}₽\n\n"
                "🎉 Теперь вы можете получить ссылку для настройки VPN."
            )
        
        from app.keyboards import get_subscription_link_keyboard
        await bot.send_message(
            chat_id=telegram_user_id,
            text=message_text,
            reply_markup=get_subscription_link_keyboard(),
            parse_mode="HTML"
        )

        # Уведомление администраторам
        from html import escape as _he
        from app.config import settings as _settings
        if _settings.ADMINS:
            _first = (telegram_user.first_name or "").strip()
            _last = (telegram_user.last_name or "").strip()
            user_full_name = f"{_first} {_last}".strip() or "Без имени"

            username_line = (
                f"Username: @{_he(telegram_user.username)}\n" if telegram_user.username else ""
            )

            plan_label = _he(plan_name)
            if period_months:
                months_word = (
                    "месяц" if period_months == 1
                    else "месяца" if period_months in (2, 3, 4)
                    else "месяцев"
                )
                plan_label = f"{plan_label} {period_months} {months_word}"

            expires_str = valid_until.strftime("%d.%m.%Y")

            currency_symbol = payment.currency.upper() if payment and payment.currency else "RUB"
            # external_id — это p.id из YooKassa (UUID формата xxxxxxxx-xxxx-...)
            external_id = payment.external_id if payment else "—"
            remna_id = subscription.remna_user_id or "—"

            # Сумма: показываем копейки только если они не нулевые
            amount_str = f"{amount:.2f}".rstrip("0").rstrip(".")
            if "." not in amount_str:
                amount_str = f"{int(amount)}"

            admin_text = (
                "💰 <b>Новая оплата VPN</b>\n\n"
                f"Пользователь: {_he(user_full_name)}\n"
                f"{username_line}"
                f"Telegram ID: <code>{telegram_user_id}</code>\n"
                f"Тариф: {plan_label}\n"
                f"Сумма: {amount_str} {_he(currency_symbol)}\n"
                f"Действует до: {expires_str}\n"
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

        # notified=True — пользователь уведомлён; admin_notified — отдельный флаг для админов
        meta = dict(meta) if isinstance(meta, dict) else {}
        meta["notified"] = True
        if _settings.ADMINS:
            meta["admin_notified"] = admin_notified
        payment.payment_metadata = meta
        await session.commit()
        
        logger.info(
            f"[{trace_id}] subscription_provisioning_success: payment_id={payment_id} "
            f"subscription_id={subscription.id} plan={plan_code} period={period_months}m "
            f"tg_id={telegram_user_id} remna_user_id={subscription.remna_user_id}"
        )
        
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
    """
    Проверяет статус платежа в YooKassa.
    Returns: dict с status/amount/... или {"error": "not_found"} если платёж не найден в YooKassa,
    или None при ошибке API/сети.
    """
    try:
        if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_API_KEY:
            logger.error("YOOKASSA_SHOP_ID и YOOKASSA_API_KEY должны быть настроены")
            return None
        
        payment = Payment.find_one(payment_id)
        
        if not payment:
            logger.warning(f"Платеж {payment_id} не найден в YooKassa")
            return {"error": "not_found"}
        
        return {
            "id": payment.id,
            "status": payment.status,
            "amount": float(payment.amount.value),
            "currency": payment.amount.currency,
            "description": payment.description,
            "metadata": payment.metadata or {},
            "paid": payment.paid,
            "created_at": payment.created_at.isoformat() if hasattr(payment, 'created_at') and payment.created_at and hasattr(payment.created_at, 'isoformat') else (str(payment.created_at) if hasattr(payment, 'created_at') and payment.created_at else None),
            "captured_at": payment.captured_at.isoformat() if hasattr(payment, 'captured_at') and payment.captured_at and hasattr(payment.captured_at, 'isoformat') else (str(payment.captured_at) if hasattr(payment, 'captured_at') and payment.captured_at else None),
        }
    except Exception as e:
        err_msg = str(e).lower()
        if "not found" in err_msg or "404" in err_msg or "не найден" in err_msg:
            logger.warning(f"Платеж {payment_id} не найден в YooKassa: {e}")
            return {"error": "not_found"}
        logger.error(f"Ошибка при проверке статуса платежа {payment_id}: {e}")
        return None


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


async def get_or_create_remna_user_and_get_subscription_url(
    telegram_user_id: int,
    subscription_id: int,
    period_months: Optional[int] = None,
) -> Optional[str]:
    """
    period_months: если передан — продлевает expireAt в Remna от текущего
    expireAt (как provision_tariff). Если None — fallback на subscription.valid_until.
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
                if telegram_user.remna_user_id:
                    # Если пользователь уже существует, обновляем expireAt и сквад
                    remna_user_id = str(telegram_user.remna_user_id)

                    # КРИТИЧНО: Обновляем expireAt в Remnawave (source of truth)
                    # Если period_months передан — продлеваем от текущего expireAt (как provision_tariff).
                    # Иначе — fallback на subscription.valid_until (старое поведение).
                    try:
                        if period_months is not None:
                            new_expire_str = await _compute_extend_expire_str(client, remna_user_id, period_months)
                            logger.info(f"remna expire_at update: remna_user_id={remna_user_id} new_expire={new_expire_str}")
                            await client.update_user(remna_user_id, expire_at=new_expire_str)
                            logger.debug(f"remna expire_at updated for remna_user_id={remna_user_id}")
                        elif subscription.valid_until:
                            # Fallback: старое поведение — используем valid_until из local DB
                            expire_at_str = normalize_expire_at(subscription.valid_until)
                            logger.info(f"remna expire_at update (fallback): remna_user_id={remna_user_id} valid_until={expire_at_str}")
                            await client.update_user(remna_user_id, expire_at=expire_at_str)
                            logger.debug(f"remna expire_at updated (fallback) for remna_user_id={remna_user_id}")
                    except Exception as expire_update_e:
                        logger.warning(f"⚠️ Не удалось обновить expireAt для пользователя {remna_user_id}: {expire_update_e}")

                    # Обновляем сквад согласно текущей подписке
                    squad_name = await get_squad_name_for_plan(subscription.plan_code)
                    if squad_name:
                        try:
                            squad = await client.get_squad_by_name(squad_name)
                            if squad:
                                squad_uuid = squad.get('uuid')
                                logger.debug(f"remna squad update: remna_user_id={remna_user_id} squad={squad_name}")
                                try:
                                    await client.update_user(remna_user_id, activeInternalSquads=[squad_uuid])
                                except Exception as squad_update_e:
                                    logger.warning(f"remna squad update failed: remna_user_id={remna_user_id} squad={squad_name} err={squad_update_e}")
                        except Exception as squad_e:
                            logger.warning(f"remna squad lookup failed: squad={squad_name} err={squad_e}")

                    subscription_url = await client.get_user_subscription_url(telegram_user.remna_user_id)
                    if subscription_url:
                        # Закрываем клиент перед возвратом
                        await client.close()
                        return subscription_url
                
                # remna_user_id не сохранён в БД — ищем по telegram_id в Remnawave
                # (пользователь мог быть создан ранее без привязки UUID к telegram_users)
                try:
                    found_remna = await client.get_user_by_telegram_id(telegram_user_id)
                except Exception:
                    found_remna = None

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
                    # Обновляем expireAt (продлеваем от текущего expireAt в Remnawave)
                    if period_months is not None:
                        try:
                            new_expire_str = await _compute_extend_expire_str(client, remna_user_id, period_months)
                            await client.update_user(remna_user_id, expire_at=new_expire_str)
                            logger.info(f"remna expire_at updated: remna_user_id={remna_user_id} new_expire={new_expire_str}")
                        except Exception as upd_e:
                            logger.warning(f"remna expire_at update failed: remna_user_id={remna_user_id} err={upd_e}")
                    # Обновляем сквад
                    _squad_name = await get_squad_name_for_plan(subscription.plan_code)
                    if _squad_name:
                        try:
                            _squad = await client.get_squad_by_name(_squad_name)
                            if _squad:
                                await client.update_user(remna_user_id, activeInternalSquads=[_squad.get("uuid")])
                                logger.debug(f"remna squad updated: remna_user_id={remna_user_id} squad={_squad_name}")
                        except Exception:
                            pass
                    # Получаем subscription URL и сохраняем
                    subscription_url = await client.get_user_subscription_url(remna_user_id)
                    if subscription_url:
                        if not subscription.config_data:
                            subscription.config_data = {}
                        subscription.config_data["subscription_url"] = subscription_url
                        subscription.remna_user_id = remna_user_id
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
                if period_months is not None and period_months > 0:
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

                logger.info(f"remna user create: tg_id={telegram_user_id} username={username} expire_at={expire_at}")
                try:
                    remna_user_data = await client.create_user(
                        username=username,
                        password=password,
                        expire_at=expire_at,
                        telegram_id=telegram_user_id,
                        active_internal_squads=[squad_uuid] if squad_uuid else None
                    )
                    logger.debug(f"remna user created: tg_id={telegram_user_id} response_keys={list(remna_user_data.keys()) if isinstance(remna_user_data, dict) else type(remna_user_data).__name__}")
                except Exception as e:
                    error_msg = str(e)
                    # Пробуем извлечь текст ошибки из response, если это HTTPStatusError
                    error_text = error_msg
                    error_json = None
                    
                    # Проверяем, есть ли response с текстом ошибки (для httpx.HTTPStatusError)
                    import httpx
                    if isinstance(e, httpx.HTTPStatusError):
                        try:
                            if hasattr(e, 'response') and e.response:
                                if hasattr(e.response, 'text') and e.response.text:
                                    error_text = e.response.text
                                    try:
                                        import json
                                        error_json = json.loads(e.response.text)
                                        if isinstance(error_json, dict):
                                            error_text = error_json.get('message', error_text)
                                            logger.warning(f"⚠️ Ошибка при создании пользователя (из JSON): {error_text}")
                                        else:
                                            logger.warning(f"⚠️ Ошибка при создании пользователя (из response.text): {error_text}")
                                    except:
                                        logger.warning(f"⚠️ Ошибка при создании пользователя (из response.text): {error_text}")
                        except Exception as parse_e:
                            logger.warning(f"⚠️ Не удалось извлечь текст ошибки: {parse_e}")
                    
                    if not error_json:
                        logger.warning(f"⚠️ Ошибка при создании пользователя: {error_msg}")
                    
                    # Если пользователь уже существует, получаем его из API
                    # Проверяем все возможные варианты ошибки
                    is_already_exists = (
                        "already exists" in error_msg.lower() or 
                        "already exists" in error_text.lower() or
                        "A019" in error_msg or 
                        "A019" in error_text or
                        (error_json and error_json.get('errorCode') == 'A019') or
                        "User username already exists" in error_msg or
                        "User username already exists" in error_text or
                        "username already exists" in error_msg.lower() or
                        "username already exists" in error_text.lower()
                    )
                    if is_already_exists:
                        logger.info(f"✅ Обнаружена ошибка 'already exists', ищем пользователя в Remna API...")
                        logger.info(f"   error_msg: {error_msg[:100]}")
                        logger.info(f"   error_text: {error_text[:100]}")
                        if error_json:
                            logger.info(f"   error_json: {error_json}")
                        logger.warning(f"⚠️ Пользователь {username} уже существует в Remna API, получаю его данные...")
                        # Ищем пользователя по telegram_id (приоритет) или username через API
                        try:
                            users_response = await client.get_users(size=100, start=1)
                            response_data = users_response.get('response', {})
                            users = response_data.get('users', [])
                            
                            found_user = None
                            # Сначала ищем по telegram_id (более надежно)
                            for user_data in users:
                                if user_data.get('telegramId') == telegram_user_id:
                                    found_user = user_data
                                    logger.info(f"✅ Найден пользователь по telegramId={telegram_user_id}")
                                    break
                            
                            # Если не нашли по telegram_id, ищем по username
                            if not found_user:
                                for user_data in users:
                                    if user_data.get('username') == username:
                                        found_user = user_data
                                        logger.info(f"✅ Найден пользователь по username={username}")
                                        break
                            
                            # Если не нашли на первой странице, ищем дальше
                            if not found_user:
                                page_size = 50
                                start = 51
                                while True:
                                    users_response = await client.get_users(size=page_size, start=start)
                                    response_data = users_response.get('response', {})
                                    users = response_data.get('users', [])
                                    
                                    if not users:
                                        break
                                    
                                    # Сначала по telegram_id
                                    for user_data in users:
                                        if user_data.get('telegramId') == telegram_user_id:
                                            found_user = user_data
                                            logger.info(f"✅ Найден пользователь по telegramId={telegram_user_id} (страница {start//page_size + 1})")
                                            break
                                    
                                    # Если не нашли, по username
                                    if not found_user:
                                        for user_data in users:
                                            if user_data.get('username') == username:
                                                found_user = user_data
                                                logger.info(f"✅ Найден пользователь по username={username} (страница {start//page_size + 1})")
                                                break
                                    
                                    if found_user or len(users) < page_size:
                                        break
                                    
                                    start += page_size
                            
                            if found_user:
                                user_uuid = found_user.get('uuid')
                                if user_uuid:
                                    logger.info(f"✅ Найден существующий пользователь: {user_uuid}")
                                    
                                    # Обновляем expireAt в Remna: продлеваем от текущего (как provision_tariff)
                                    if period_months is not None and period_months > 0:
                                        from datetime import timezone as _tz
                                        from dateutil.relativedelta import relativedelta as _rd
                                        _now_utc = datetime.now(_tz.utc)
                                        _base = _now_utc
                                        _expire_raw = found_user.get("expireAt") or found_user.get("expires_at")
                                        if _expire_raw:
                                            try:
                                                _exp_str = str(_expire_raw).replace("Z", "+00:00")
                                                _current_exp = datetime.fromisoformat(_exp_str)
                                                if _current_exp.tzinfo is None:
                                                    _current_exp = _current_exp.replace(tzinfo=_tz.utc)
                                                else:
                                                    _current_exp = _current_exp.astimezone(_tz.utc)
                                                if _current_exp > _now_utc:
                                                    _base = _current_exp
                                                    logger.info(f"📝 Продлеваю expireAt от текущего: {_current_exp.isoformat()}")
                                            except Exception:
                                                pass
                                        _new_expire_str = (_base + _rd(months=period_months)).strftime("%Y-%m-%dT%H:%M:%SZ")
                                        logger.info(f"📝 Обновляю expireAt для пользователя {user_uuid}: {_new_expire_str}")
                                        try:
                                            await client.update_user(str(user_uuid), expire_at=_new_expire_str)
                                            logger.info(f"✅ expireAt обновлен в Remna")
                                        except Exception as update_e:
                                            logger.warning(f"⚠️ Не удалось обновить expireAt: {update_e}")
                                    elif subscription.valid_until:
                                        logger.info(f"📝 Обновляю expireAt для пользователя {user_uuid}: {subscription.valid_until} (fallback)")
                                        try:
                                            await client.update_user(str(user_uuid), expire_at=subscription.valid_until)
                                            logger.info(f"✅ expireAt обновлен в Remna")
                                        except Exception as update_e:
                                            logger.warning(f"⚠️ Не удалось обновить expireAt: {update_e}")
                                    
                                    # Обновляем telegramId если его нет
                                    if not found_user.get('telegramId'):
                                        logger.info(f"📝 Обновляю telegramId для пользователя {user_uuid}")
                                        try:
                                            await client.update_user(str(user_uuid), telegramId=int(telegram_user_id))
                                            logger.info(f"✅ telegramId обновлен")
                                        except Exception as update_e:
                                            logger.warning(f"⚠️ Не удалось обновить telegramId: {update_e}")
                                    
                                    # Обновляем сквад если нужно
                                    squad_name = await get_squad_name_for_plan(subscription.plan_code)
                                    if squad_name:
                                        try:
                                            squad = await client.get_squad_by_name(squad_name)
                                            if squad:
                                                squad_uuid = squad.get('uuid')
                                                logger.info(f"📝 Обновляю сквад для пользователя {user_uuid}: {squad_name}")
                                                try:
                                                    await client.update_user(str(user_uuid), activeInternalSquads=[squad_uuid])
                                                    logger.info(f"✅ Сквад обновлен в Remna")
                                                except Exception as squad_update_e:
                                                    logger.warning(f"⚠️ Не удалось обновить сквад: {squad_update_e}")
                                        except Exception as squad_e:
                                            logger.warning(f"⚠️ Ошибка при получении сквада {squad_name}: {squad_e}")
                                    
                                    # Получаем полные данные пользователя
                                    remna_user_data = await client.get_user_by_id(str(user_uuid))
                                    logger.info(f"✅ Получены данные существующего пользователя")
                                    logger.info(f"📋 Структура данных пользователя: {list(remna_user_data.keys()) if isinstance(remna_user_data, dict) else type(remna_user_data)}")
                                    if isinstance(remna_user_data, dict):
                                        logger.info(f"📋 Ключи в response: {list(remna_user_data.get('response', {}).keys()) if isinstance(remna_user_data.get('response'), dict) else 'response не dict'}")
                                    
                                    # Пытаемся извлечь subscription URL напрямую из данных пользователя
                                    subscription_url = None
                                    if isinstance(remna_user_data, dict):
                                        # Проверяем разные варианты структуры
                                        subscription_url = (
                                            remna_user_data.get('subscriptionUrl') or 
                                            remna_user_data.get('subscription_url') or
                                            (remna_user_data.get('response', {}) or {}).get('subscriptionUrl') or
                                            (remna_user_data.get('response', {}) or {}).get('subscription_url')
                                        )
                                        
                                        # Если нашли token, формируем URL
                                        if not subscription_url:
                                            subscription_token = (
                                                remna_user_data.get('subscriptionToken') or 
                                                remna_user_data.get('subscription_token') or
                                                (remna_user_data.get('response', {}) or {}).get('subscriptionToken') or
                                                (remna_user_data.get('response', {}) or {}).get('subscription_token')
                                            )
                                            if subscription_token:
                                                subscription_url = f"https://sub.crs-projects.com/{subscription_token}"
                                                logger.info(f"✅ Сформирован subscription URL из token: {subscription_url[:50]}...")
                                    
                                    # Если не нашли в данных, используем метод клиента
                                    if not subscription_url:
                                        subscription_url = await client.get_user_subscription_url(str(user_uuid))
                                    
                                    if subscription_url and subscription_url.strip():
                                        subscription_url = subscription_url.strip()
                                        logger.info(f"✅ Получен subscription URL для существующего пользователя: {subscription_url[:50]}...")
                                        
                                        # Сохраняем remna_user_id и subscription_url в БД
                                        remna_user_result = await session.execute(
                                            select(RemnaUser).where(RemnaUser.remna_id == str(user_uuid))
                                        )
                                        remna_user = remna_user_result.scalar_one_or_none()
                                        
                                        if not remna_user:
                                            remna_user = RemnaUser(
                                                remna_id=str(user_uuid),
                                                username=username,
                                                raw_data=remna_user_data if isinstance(remna_user_data, dict) else {}
                                            )
                                            session.add(remna_user)
                                        
                                        telegram_user.remna_user_id = str(user_uuid)
                                        subscription.remna_user_id = str(user_uuid)
                                        
                                        if not subscription.config_data:
                                            subscription.config_data = {}
                                        subscription.config_data["subscription_url"] = subscription_url
                                        
                                        await session.commit()
                                        logger.info(f"✅ Данные сохранены в БД для существующего пользователя")
                                        
                                        await client.close()
                                        return subscription_url
                                    else:
                                        logger.warning(f"⚠️ Не удалось получить subscription URL для существующего пользователя {user_uuid}")
                                        logger.debug(f"Данные пользователя: {remna_user_data}")
                                        # Обновляем remna_user_id в БД даже если URL не получен
                                        telegram_user.remna_user_id = str(user_uuid)
                                        subscription.remna_user_id = str(user_uuid)
                                        await session.commit()
                                        await client.close()
                                        return None
                                else:
                                    logger.error(f"❌ Не удалось получить UUID существующего пользователя")
                                    return None
                            else:
                                logger.error(f"❌ Пользователь {username} не найден в API после ошибки 'already exists'")
                                return None
                        except Exception as search_e:
                            logger.error(f"❌ Ошибка при поиске существующего пользователя: {search_e}")
                            return None
                    else:
                        logger.error(f"Ошибка при создании пользователя в Remna API: {e}")
                        import traceback
                        logger.debug(traceback.format_exc())
                        return None
                
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
                            subscription_url = f"https://sub.crs-projects.com/{token}"
                
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
                        raw_data=user_response_data if user_response_data else remna_user_data
                    )
                    session.add(remna_user)
                    logger.info(f"Создана запись в remna_users для remna_id={remna_user_id}")
                else:
                    # Обновляем существующую запись
                    remna_user.username = username
                    remna_user.raw_data = user_response_data if user_response_data else remna_user_data
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
                    logger.info(f"✅ Subscription URL сохранен в config_data: {subscription_url[:50]}...")
                else:
                    logger.warning(f"⚠️ Subscription URL не найден в ответе создания пользователя")
                
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
                        logger.info(f"✅ Subscription URL получен и сохранен: {subscription_url[:50]}...")
                    else:
                        logger.error(f"❌ Не удалось получить subscription URL для remna_user_id={remna_user_id}")
                
                # Обновляем пользователя для добавления Telegram ID (если его не было в ответе)
                # expireAt и настройки internal squads
                try:
                    update_payload = {}
                    
                    # Обновляем expireAt в Remna для нового пользователя (клиент нормализует и маппит expire_at -> expireAt)
                    if subscription.valid_until:
                        update_payload["expire_at"] = subscription.valid_until
                        logger.info(f"📝 Обновляю expireAt для нового пользователя")
                    
                    # Убеждаемся, что telegramId установлен
                    if user_response_data and not user_response_data.get("telegramId"):
                        update_payload["telegramId"] = int(telegram_user_id)  # API ожидает число
                    
                    # Добавляем internal squads в зависимости от плана подписки
                    squad_name = await get_squad_name_for_plan(subscription.plan_code)
                    if squad_name:
                        try:
                            squad = await client.get_squad_by_name(squad_name)
                            if squad:
                                squad_uuid = squad.get('uuid')
                                if squad_uuid:
                                    # Получаем текущие сквады пользователя
                                    # Если user_response_data еще не содержит актуальные данные, получаем их заново
                                    if not user_response_data or 'activeInternalSquads' not in user_response_data:
                                        user_data = await client.get_user_by_id(str(remna_user_id))
                                        if isinstance(user_data, dict):
                                            if "response" in user_data and isinstance(user_data["response"], dict):
                                                user_response_data = user_data["response"]
                                            else:
                                                user_response_data = user_data
                                    
                                    current_squads = user_response_data.get('activeInternalSquads', []) if user_response_data else []
                                    current_squad_uuids = []
                                    for s in current_squads:
                                        if isinstance(s, dict):
                                            current_squad_uuids.append(s.get('uuid'))
                                        elif isinstance(s, str):
                                            current_squad_uuids.append(s)
                                    
                                    # Если нужного сквада нет, добавляем его
                                    if squad_uuid not in current_squad_uuids:
                                        current_squad_uuids.append(squad_uuid)
                                        update_payload["activeInternalSquads"] = current_squad_uuids
                                        logger.info(f"📝 Добавлен сквад {squad_name} ({squad_uuid}) для плана {subscription.plan_code}")
                                    else:
                                        logger.info(f"✅ Сквад {squad_name} уже назначен пользователю")
                            else:
                                logger.warning(f"⚠️ Сквад {squad_name} не найден в Remnawave")
                        except Exception as squad_e:
                            logger.warning(f"⚠️ Ошибка при получении сквада {squad_name}: {squad_e}")
                    
                    if update_payload:
                        logger.info(f"Обновление пользователя в Remna API: {update_payload}")
                        await client.update_user(str(remna_user_id), **update_payload)
                        logger.info(f"Пользователь обновлен в Remna API")
                except Exception as e:
                    logger.warning(f"Не удалось обновить пользователя в Remna API (не критично): {e}")
                
                return subscription_url
                
            finally:
                await client.close()
                
    except Exception as e:
        logger.error(f"Ошибка при получении subscription URL: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return None