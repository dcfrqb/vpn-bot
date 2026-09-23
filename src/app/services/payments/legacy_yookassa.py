"""2.x payment helpers (release 3.0: legacy, deleted at the cutover).

Imported as ``app.services.payments.yookassa`` too (that module is an alias of
this one). The 2.x provisioning (Phase A/B/C, handle_successful_payment,
get_or_create_remna_user_and_get_subscription_url, verification) is GONE:
payments are granted by app.services.fulfillment through the
ProvisioningService port (stream B, app.services.provisioning).

What is left here:
- ``create_payment``: 2.x screens that still create payments themselves
  (obhod packages) - async client, price checked server-side;
- ``check_payment_status``: provider view over the async gateway (refunds.py);
- ``resync_subscription_to_remnawave``: the 2.x reconciler's shallow scan,
  now a grant of the DB date through the ProvisioningService port;
- ``process_payment_webhook``: alias of app.services.payments.webhook;
- ``generate_remna_password``, ``VALID_STATUS_TRANSITIONS``.
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import secrets
import string
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.models import Payment as PaymentModel, TelegramUser
from app.db.session import SessionLocal
from app.logger import logger
from app.services.payments.pricing import price_mismatch_reason as _price_mismatch_reason  # noqa: F401
from app.services.payments.webhook import process_payment_webhook  # noqa: F401 (2.x import path)

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



async def resync_subscription_to_remnawave(subscription_id: int, trace_id: Optional[str] = None) -> bool:
    """2.x reconciler (shallow scan): push the DB term of an active main
    subscription to the panel. 3.0: one grant through the ProvisioningService
    port with ``until = valid_until`` (never shortens the panel date; the
    idempotency key is the subscription and the date). True when applied."""
    from app.domain.models import Entitlement, EntitlementSource
    from app.db.models import Subscription

    trace_id = trace_id or f"resync:{subscription_id}"
    if not SessionLocal:
        return False
    async with SessionLocal() as session:
        sub = (await session.execute(
            select(Subscription).where(Subscription.id == int(subscription_id), Subscription.sub_kind == "main")
        )).scalar_one_or_none()
    if sub is None or (not sub.active and not sub.is_lifetime) or not sub.plan_code:
        return False
    if not sub.is_lifetime and sub.valid_until is None:
        return False
    until = sub.valid_until.replace(tzinfo=timezone.utc) if sub.valid_until else None
    ent = Entitlement(
        plan_code=sub.plan_code, source=EntitlementSource.ADMIN, until=None if sub.is_lifetime else until,
        is_lifetime=bool(sub.is_lifetime), note="2.x reconciler resync",
    )
    key = f"resync:{sub.id}:{until:%Y%m%d%H%M}" if until else f"resync:{sub.id}:lifetime"
    try:
        from app.container import get_container

        await get_container().provisioning.grant(int(sub.telegram_user_id), ent, trace_id=key)
    except Exception as e:  # noqa: BLE001 - the reconciler counts and alerts
        logger.warning(f"[{trace_id}] resync failed: subscription_id={subscription_id} ({type(e).__name__}: {e})")
        return False
    return True
