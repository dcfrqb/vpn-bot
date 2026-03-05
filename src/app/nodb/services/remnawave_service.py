"""
Remnawave — единственный источник истины в No-DB режиме.
ensure_user, provision_subscription.
REMNAWAVE_DRY_RUN=1 — для локальной проверки без вызова API (только логирование).
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.remnawave.client import RemnaClient
from app.logger import logger
from app.nodb.logs import log_payment_event, EVENT_REMNAWAVE_PROVISION_SUCCESS, EVENT_REMNAWAVE_PROVISION_FAILED

# Маппинг tariff_code -> (plan_code, months)
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
}


async def ensure_user(tg_id: int, username: Optional[str] = None, name: Optional[str] = None) -> Optional[str]:
    """
    Найти/создать пользователя в Remnawave.
    Возвращает remna_user_id (uuid) или None при ошибке.
    """
    client = RemnaClient()
    try:
        existing = await client.get_user_by_telegram_id(tg_id)
        if existing:
            return existing.uuid

        display_name = name or username or f"User_{tg_id}"
        user = await client.create_user_with_name(telegram_id=tg_id, name=display_name)
        return user.uuid
    except Exception as e:
        logger.error(f"Ошибка ensure_user для tg_id={tg_id}: {e}")
        return None
    finally:
        await client.close()


async def provision_subscription(
    remnawave_user_id: str,
    tariff_code: str,
    req_id: Optional[str] = None,
    tg_id: Optional[int] = None,
) -> bool:
    """
    Выдать/продлить подписку в Remnawave.
    tariff_code: BASIC_3M, premium_6 и т.д.
    Возвращает True при успехе.
    """
    client = RemnaClient()
    try:
        plan_code, period_months = TARIFF_TO_PLAN.get(
            tariff_code, TARIFF_TO_PLAN.get(tariff_code.upper(), ("basic", 1))
        )
    except Exception:
        plan_code, period_months = "basic", 1

    period_days = period_months * 30
    valid_until = datetime.now(timezone.utc) + timedelta(days=period_days)
    valid_until_str = valid_until.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        await client.update_user(remnawave_user_id, expire_at=valid_until_str)

        squad_name = "premium" if plan_code == "premium" else "basic"
        try:
            squad = await client.get_squad_by_name(squad_name)
            if squad and squad.get("uuid"):
                await client.update_user(remnawave_user_id, activeInternalSquads=[squad["uuid"]])
        except Exception as squad_e:
            logger.warning(f"Не удалось обновить сквад {squad_name}: {squad_e}")

        log_payment_event(
            EVENT_REMNAWAVE_PROVISION_SUCCESS,
            req_id=req_id,
            tg_id=tg_id,
            payload={
                "remna_user_id": remnawave_user_id,
                "tariff": tariff_code,
                "valid_until": valid_until_str,
            },
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка provision_subscription для tariff={tariff_code}: {e}")
        log_payment_event(
            EVENT_REMNAWAVE_PROVISION_FAILED,
            req_id=req_id,
            tg_id=tg_id,
            payload={"error": str(e)[:500], "tariff": tariff_code},
        )
        return False
    finally:
        await client.close()


async def provision_tariff(
    telegram_id: int,
    tariff: str,
    req_id: Optional[str] = None,
) -> bool:
    """
    Выдаёт доступ пользователю в Remnawave по тарифу.
    Сначала ensure_user, затем provision_subscription.
    REMNAWAVE_DRY_RUN=1 — не дергает сеть, логирует remnawave_provision_success фейково.
    """
    if os.environ.get("REMNAWAVE_DRY_RUN") == "1":
        valid_until = datetime.now(timezone.utc) + timedelta(days=30)
        log_payment_event(
            EVENT_REMNAWAVE_PROVISION_SUCCESS,
            req_id=req_id,
            tg_id=telegram_id,
            payload={"dry_run": True, "tariff": tariff, "valid_until": valid_until.strftime("%Y-%m-%dT%H:%M:%SZ")},
        )
        logger.info(f"[DRY-RUN] provision_tariff: tg_id={telegram_id} tariff={tariff} req_id={req_id}")
        return True

    remna_user_id = await ensure_user(telegram_id)
    if not remna_user_id:
        log_payment_event(
            EVENT_REMNAWAVE_PROVISION_FAILED,
            req_id=req_id,
            tg_id=telegram_id,
            payload={"error": "ensure_user_failed"},
        )
        return False

    return await provision_subscription(
        remnawave_user_id=remna_user_id,
        tariff_code=tariff,
        req_id=req_id,
        tg_id=telegram_id,
    )
