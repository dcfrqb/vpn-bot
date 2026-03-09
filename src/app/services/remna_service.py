"""
Сервис интеграции с Remnawave.
Remnawave — единственный источник правды по пользователям и подпискам.
Календарные месяцы: relativedelta(months=N), base = max(now, current_expires_at).
"""
from datetime import datetime, timezone
from typing import Optional

from dateutil.relativedelta import relativedelta

from app.remnawave.client import RemnaClient, LIFETIME_EXPIRE_AT
from app.logger import logger
from app.services.jsonl_logger import log_payment_event, EVENT_REMNAWAVE_PROVISION_SUCCESS, EVENT_REMNAWAVE_PROVISION_FAILED


# Маппинг тарифов на plan_code и период
TARIFF_TO_PLAN = {
    "PRO_1M": ("premium", 1),
    "PRO_3M": ("premium", 3),
    "PRO_6M": ("premium", 6),
    "PRO_12M": ("premium", 12),
    "BASIC_1M": ("basic", 1),
    "BASIC_3M": ("basic", 3),
    "BASIC_6M": ("basic", 6),
    "BASIC_12M": ("basic", 12),
    "basic_1": ("basic", 1),
    "basic_3": ("basic", 3),
    "basic_6": ("basic", 6),
    "basic_12": ("basic", 12),
    "premium_1": ("premium", 1),
    "premium_3": ("premium", 3),
    "premium_6": ("premium", 6),
    "premium_12": ("premium", 12),
    "premium_forever": ("premium", -1),  # -1 = unlimited
}


async def ensure_user_in_remnawave(
    telegram_id: int,
    username: Optional[str] = None,
    name: Optional[str] = None,
    tg_first_name: Optional[str] = None,
    tg_last_name: Optional[str] = None,
) -> Optional[str]:
    """
    Получает или создаёт пользователя в Remnawave.
    Возвращает remna_user_id (uuid) или None при ошибке.

    Логика:
    1. Найти по telegram_id → использовать
    2. Не найден → создать с username по build_remna_username()
    """
    client = RemnaClient()
    try:
        user = await client.get_or_create_user(
            telegram_id=telegram_id,
            tg_username=username,
            tg_first_name=tg_first_name,
            tg_last_name=tg_last_name,
        )
        return user.uuid
    except Exception as e:
        logger.error(f"Ошибка ensure_user_in_remnawave для tg_id={telegram_id}: {e}")
        return None
    finally:
        await client.close()


async def provision_tariff(
    telegram_id: int,
    tariff: str,
    req_id: Optional[str] = None,
) -> bool:
    """
    Выдаёт доступ пользователю в Remnawave по тарифу.
    tariff: PRO_1M, BASIC_1M, basic_1, premium_3, premium_forever и т.д.
    Календарные месяцы, продление от текущего expireAt если активна подписка.
    Возвращает True при успехе.
    """
    client = RemnaClient()
    try:
        plan_code, period_months = TARIFF_TO_PLAN.get(
            tariff, TARIFF_TO_PLAN.get(tariff.upper(), ("basic", 1))
        )
    except Exception:
        plan_code, period_months = "basic", 1

    try:
        remna_user_id = await ensure_user_in_remnawave(telegram_id)
        if not remna_user_id:
            log_payment_event(
                EVENT_REMNAWAVE_PROVISION_FAILED,
                req_id=req_id,
                tg_id=telegram_id,
                payload={"error": "ensure_user_failed"},
            )
            return False

        # Определяем valid_until
        if period_months < 0:
            valid_until_str = LIFETIME_EXPIRE_AT
        else:
            now = datetime.now(timezone.utc)
            base = now
            # Продление от текущего expireAt если ещё активна
            try:
                user_data = await client.get_user_by_id(remna_user_id)
                raw = user_data.get("response", user_data) if isinstance(user_data, dict) else {}
                if not isinstance(raw, dict):
                    raw = {}
                expire_raw = raw.get("expireAt") or raw.get("expires_at") or raw.get("valid_until")
                if expire_raw:
                    if isinstance(expire_raw, str):
                        expire_str = expire_raw.replace("Z", "+00:00")
                        if "+" not in expire_str and "-" not in expire_str[-6:]:
                            expire_str += "+00:00"
                        current_exp = datetime.fromisoformat(expire_str)
                    else:
                        current_exp = datetime.fromtimestamp(expire_raw)
                    if current_exp.tzinfo:
                        current_exp = current_exp.astimezone(timezone.utc)
                    else:
                        current_exp = current_exp.replace(tzinfo=timezone.utc)
                    if current_exp > now:
                        base = current_exp
            except Exception as e:
                logger.debug(f"Не удалось получить текущий expireAt для {remna_user_id}: {e}")
            valid_until = base + relativedelta(months=period_months)
            valid_until_str = valid_until.strftime("%Y-%m-%dT%H:%M:%SZ")

        await client.update_user(remna_user_id, expire_at=valid_until_str)

        squad_name = "premium" if plan_code == "premium" else "basic"
        try:
            squad = await client.get_squad_by_name(squad_name)
            if squad and squad.get("uuid"):
                await client.update_user(remna_user_id, activeInternalSquads=[squad["uuid"]])
        except Exception as squad_e:
            logger.warning(f"Не удалось обновить сквад {squad_name}: {squad_e}")

        log_payment_event(
            EVENT_REMNAWAVE_PROVISION_SUCCESS,
            req_id=req_id,
            tg_id=telegram_id,
            payload={"remna_user_id": remna_user_id, "tariff": tariff, "valid_until": valid_until_str},
        )

        # Инвалидируем кэш, чтобы статус обновился сразу
        try:
            from app.services.cache import invalidate_subscription_cache, invalidate_sync_cache
            await invalidate_subscription_cache(telegram_id)
            await invalidate_sync_cache(telegram_id)
            logger.debug(f"Кэш инвалидирован после provision_tariff для {telegram_id}")
        except Exception as cache_e:
            logger.warning(f"Не удалось инвалидировать кэш для {telegram_id}: {cache_e}")

        return True
    except Exception as e:
        logger.error(f"Ошибка provision_tariff для tg_id={telegram_id} tariff={tariff}: {e}")
        log_payment_event(
            EVENT_REMNAWAVE_PROVISION_FAILED,
            req_id=req_id,
            tg_id=telegram_id,
            payload={"error": str(e)[:500], "tariff": tariff},
        )
        return False
    finally:
        await client.close()
