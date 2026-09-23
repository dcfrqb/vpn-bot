"""2.x payment helpers still used in 3.0 (retire in 3.0.1).

Imported as ``app.services.payments.yookassa`` too (that module is an alias of
this one). The 2.x provisioning (Phase A/B/C, handle_successful_payment,
get_or_create_remna_user_and_get_subscription_url, verification) is GONE:
payments are granted by app.services.fulfillment through the
ProvisioningService port (stream B, app.services.provisioning).

What is left here:
- ``check_payment_status``: provider view over the async gateway (refunds.py);
- ``resync_subscription_to_remnawave``: the 2.x reconciler's shallow scan,
  now a grant of the DB date through the ProvisioningService port;
- ``process_payment_webhook``: alias of app.services.payments.webhook;
- ``generate_remna_password``, ``VALID_STATUS_TRANSITIONS``.
"""
from datetime import timezone
from typing import Any, Dict, Optional
import secrets
import string

from sqlalchemy import select

from app.config import settings
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
