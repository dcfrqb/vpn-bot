"""
Сервис пользователей — данные только из Remnawave.
БД удалена.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select

from app.core.plans import LEGACY_CUTOFF
from app.remnawave.client import RemnaClient
from app.services.remna_service import ensure_user_in_remnawave
from app.logger import logger


# Cohort-кэш в Redis: ключ legacy:{tg_id} → b"1"/b"0", TTL 5 минут.
# Используется для аналитики/админки. В UI больше не применяется —
# меню тарифов одно для всех (см. MENU_PLAN_CODES).
_LEGACY_CACHE_TTL_SEC = 300
_LEGACY_CACHE_PREFIX = "legacy:"

# Last-plan кэш для кнопки "🔄 Продлить". Короткий TTL: после оплаты вызывается
# invalidate_last_plan_cache, между оплатами план меняется редко.
_LAST_PLAN_CACHE_TTL_SEC = 60
_LAST_PLAN_CACHE_PREFIX = "last_plan:"
# Sentinel значение в кэше, означающее "нет последнего плана у юзера".
_LAST_PLAN_NONE_SENTINEL = b"-"


@dataclass
class SubscriptionInfo:
    """Минимальная информация о подписке (из Remna)"""
    active: bool
    valid_until: Optional[datetime]
    plan_code: str
    plan_name: str
    remna_user_id: Optional[str]
    config_data: dict


async def get_user_active_subscription(telegram_id: int, use_cache: bool = True) -> Optional[SubscriptionInfo]:
    """
    Получает активную подписку пользователя из Remnawave.
    Возвращает SubscriptionInfo или None.
    """
    if use_cache:
        try:
            from app.services.cache import get_cached_sync_result
            cached = await get_cached_sync_result(telegram_id)
            if cached and cached.get("status") == "active":
                return SubscriptionInfo(
                    active=True,
                    valid_until=None,
                    plan_code="",
                    plan_name="",
                    remna_user_id=cached.get("remna_uuid"),
                    config_data={},
                )
            if cached and cached.get("status") in ("expired", "none"):
                return None
        except Exception:
            pass

    try:
        client = RemnaClient()
        result = await client.get_user_with_subscription_by_telegram_id(telegram_id)
        await client.close()

        if not result:
            return None

        remna_user, remna_subscription = result
        if not remna_subscription or not remna_subscription.active:
            return None

        return SubscriptionInfo(
            active=True,
            valid_until=remna_subscription.expires_at,
            plan_code=remna_subscription.plan or "",
            plan_name=remna_subscription.plan or "",
            remna_user_id=remna_user.uuid,
            config_data={},
        )
    except Exception as e:
        logger.debug(f"Ошибка получения подписки для {telegram_id}: {e}")
        return None


async def update_user_activity(user_id: int) -> None:
    """Заглушка: обновление активности пользователя."""
    pass


async def is_legacy_user(telegram_id: int) -> bool:
    """
    True если у юзера есть Payment(status='succeeded', provider != 'promo')
    с paid_at/created_at < LEGACY_CUTOFF.

    Используется для разводки тарифной сетки: legacy юзеры видят старые
    basic/premium со старыми ценами; новые — lite/standard/pro по новой сетке.

    Fail-safe: при любой ошибке (Redis/БД недоступны) возвращает False —
    т.е. fallback в new-cohort. Это даёт ARPU-приоритет: legacy в худшем
    случае увидят новые цены и заплатят больше, но при этом всё равно
    провизионятся в правильный squad по plan_code из payment.metadata.
    """
    # 1. Redis cache lookup
    try:
        from app.services.cache import get_redis_client
        client = get_redis_client()
        if client is not None:
            cached = await client.get(f"{_LEGACY_CACHE_PREFIX}{telegram_id}")
            if cached is not None:
                return cached in (b"1", "1", 1, True)
    except Exception as e:
        logger.debug(f"is_legacy_user redis-get soft-fail tg_id={telegram_id}: {e}")

    # 2. БД
    try:
        from app.db.models import Payment
        from app.db.session import SessionLocal

        if SessionLocal is None:
            return False

        async with SessionLocal() as session:
            stmt = (
                select(func.count())
                .select_from(Payment)
                .where(
                    Payment.telegram_user_id == telegram_id,
                    Payment.status == "succeeded",
                    Payment.provider != "promo",
                    func.coalesce(Payment.paid_at, Payment.created_at) < LEGACY_CUTOFF,
                )
            )
            count = (await session.execute(stmt)).scalar_one() or 0
            result = count > 0
    except Exception as e:
        logger.warning(f"is_legacy_user db-error tg_id={telegram_id}: {e} → fail-open new-cohort")
        return False

    # 3. Положить в кэш
    try:
        from app.services.cache import get_redis_client
        client = get_redis_client()
        if client is not None:
            await client.setex(
                f"{_LEGACY_CACHE_PREFIX}{telegram_id}",
                _LEGACY_CACHE_TTL_SEC,
                b"1" if result else b"0",
            )
    except Exception as e:
        logger.debug(f"is_legacy_user redis-set soft-fail tg_id={telegram_id}: {e}")

    return result


async def get_user_last_plan(telegram_id: int) -> Optional[str]:
    """
    Возвращает plan_code последней покупаемой подписки юзера — для кнопки "🔄 Продлить".

    Логика:
    1. Если есть active Subscription с покупаемым plan_code (basic/premium/lite/standard/pro) — возвращаем его.
    2. Иначе — последний succeeded Payment с непустой metadata.plan_code и provider != 'promo'.
    3. Иначе None (юзер новый или у него только promo).

    Не возвращает 'trial' (это служебный код, не покупается).

    Кэш Redis 60с. После оплаты yookassa-ом вызывается invalidate_last_plan_cache.
    Fail-safe: при любой ошибке возвращает None — кнопка просто не покажется.
    """
    # Покупаемые коды (исключаем trial).
    from app.core.plans import LEGACY_PLAN_CODES, NEW_PLAN_CODES
    purchasable = set(LEGACY_PLAN_CODES) | set(NEW_PLAN_CODES)

    # 1. Redis cache lookup
    try:
        from app.services.cache import get_redis_client
        client = get_redis_client()
        if client is not None:
            cached = await client.get(f"{_LAST_PLAN_CACHE_PREFIX}{telegram_id}")
            if cached is not None:
                if cached in (_LAST_PLAN_NONE_SENTINEL, "-"):
                    return None
                # bytes → str
                if isinstance(cached, bytes):
                    cached = cached.decode("ascii", errors="ignore")
                return cached if cached in purchasable else None
    except Exception as e:
        logger.debug(f"get_user_last_plan redis-get soft-fail tg_id={telegram_id}: {e}")

    # 2. БД
    plan_code: Optional[str] = None
    try:
        from sqlalchemy import desc, func, select as _select

        from app.db.models import Payment, Subscription
        from app.db.session import SessionLocal

        if SessionLocal is None:
            return None

        async with SessionLocal() as session:
            # 2a. Active subscription
            sub_stmt = (
                _select(Subscription.plan_code)
                .where(
                    Subscription.telegram_user_id == telegram_id,
                    Subscription.active.is_(True),
                )
                .order_by(desc(Subscription.updated_at))
                .limit(1)
            )
            sub_row = (await session.execute(sub_stmt)).first()
            if sub_row and sub_row[0] in purchasable:
                plan_code = sub_row[0]

            # 2b. Если активной нет — последний succeeded непромо-платёж.
            if plan_code is None:
                pay_stmt = (
                    _select(Payment.payment_metadata)
                    .where(
                        Payment.telegram_user_id == telegram_id,
                        Payment.status == "succeeded",
                        Payment.provider != "promo",
                    )
                    .order_by(desc(func.coalesce(Payment.paid_at, Payment.created_at)))
                    .limit(1)
                )
                pay_row = (await session.execute(pay_stmt)).first()
                if pay_row and pay_row[0]:
                    metadata = pay_row[0]
                    if isinstance(metadata, dict):
                        candidate = metadata.get("plan_code")
                        if isinstance(candidate, str) and candidate.lower() in purchasable:
                            plan_code = candidate.lower()
    except Exception as e:
        logger.warning(f"get_user_last_plan db-error tg_id={telegram_id}: {e} → None")
        return None

    # 3. Положить в кэш (даже None — sentinel, чтобы не дёргать БД повторно).
    try:
        from app.services.cache import get_redis_client
        client = get_redis_client()
        if client is not None:
            value = plan_code.encode("ascii") if plan_code else _LAST_PLAN_NONE_SENTINEL
            await client.setex(
                f"{_LAST_PLAN_CACHE_PREFIX}{telegram_id}",
                _LAST_PLAN_CACHE_TTL_SEC,
                value,
            )
    except Exception as e:
        logger.debug(f"get_user_last_plan redis-set soft-fail tg_id={telegram_id}: {e}")

    return plan_code


async def invalidate_last_plan_cache(telegram_id: int) -> None:
    """Сбросить кэш последнего плана. Зовётся после успешной оплаты/провижна."""
    try:
        from app.services.cache import get_redis_client
        client = get_redis_client()
        if client is not None:
            await client.delete(f"{_LAST_PLAN_CACHE_PREFIX}{telegram_id}")
    except Exception as e:
        logger.debug(f"invalidate_last_plan_cache soft-fail tg_id={telegram_id}: {e}")


async def invalidate_legacy_cohort_cache(telegram_id: int) -> None:
    """Принудительный сброс кэша cohort'а (если решили вручную перевести юзера)."""
    try:
        from app.services.cache import get_redis_client
        client = get_redis_client()
        if client is not None:
            await client.delete(f"{_LEGACY_CACHE_PREFIX}{telegram_id}")
    except Exception as e:
        logger.debug(f"invalidate_legacy_cohort_cache soft-fail tg_id={telegram_id}: {e}")


async def get_or_create_telegram_user(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    language_code: Optional[str] = None,
):
    """
    Заглушка: создаёт/проверяет пользователя в Remnawave.
    Возвращает объект с telegram_id, username, first_name для совместимости.
    """
    name = first_name or username or f"User_{telegram_id}"
    remna_user_id = await ensure_user_in_remnawave(telegram_id, username=username, name=name)
    return type("User", (), {
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "language_code": language_code,
        "remna_user_id": remna_user_id,
        "created_at": datetime.utcnow(),
    })()
