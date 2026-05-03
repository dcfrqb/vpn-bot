"""
Сервис интеграции с Remnawave.
Remnawave — единственный источник правды по пользователям и подпискам.
Календарные месяцы: relativedelta(months=N), base = max(now, current_expires_at).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from dateutil.relativedelta import relativedelta

from app.remnawave.client import RemnaClient, LIFETIME_EXPIRE_AT
from app.logger import logger
from app.services.jsonl_logger import log_payment_event, EVENT_REMNAWAVE_PROVISION_SUCCESS, EVENT_REMNAWAVE_PROVISION_FAILED


# Жёсткий timeout на отдельный Remnawave-вызов в provision-пути.
# Внутренние retry клиента могут съесть до 90с; webhook-хендлер не должен так висеть.
# При превышении — asyncio.TimeoutError, caller помечает payment.needs_provisioning=True,
# recovery task дожмёт асинхронно.
REMNAWAVE_CALL_TIMEOUT = 20.0


# Маппинг тарифов на plan_code и период (в месяцах).
#
# LEGACY (basic/premium) сохраняем как было — продление старых юзеров идёт
# через эти же ключи с теми же squad-именами в Remnawave.
#
# NEW (lite/standard/pro) — новая когорта; squads тех же имён должны
# существовать в Remnawave (см. PLAN_CATALOG).
TARIFF_TO_PLAN = {
    # Legacy uppercase aliases (исторические)
    "PRO_1M": ("premium", 1),
    "PRO_3M": ("premium", 3),
    "PRO_6M": ("premium", 6),
    "PRO_12M": ("premium", 12),
    "BASIC_1M": ("basic", 1),
    "BASIC_3M": ("basic", 3),
    "BASIC_6M": ("basic", 6),
    "BASIC_12M": ("basic", 12),
    # Legacy lowercase
    "basic_1": ("basic", 1),
    "basic_3": ("basic", 3),
    "basic_6": ("basic", 6),
    "basic_12": ("basic", 12),
    "premium_1": ("premium", 1),
    "premium_3": ("premium", 3),
    "premium_6": ("premium", 6),
    "premium_12": ("premium", 12),
    "premium_forever": ("premium", -1),  # -1 = unlimited
    # NEW cohort
    "lite_1": ("lite", 1),
    "lite_3": ("lite", 3),
    "lite_6": ("lite", 6),
    "lite_12": ("lite", 12),
    "standard_1": ("standard", 1),
    "standard_3": ("standard", 3),
    "standard_6": ("standard", 6),
    "standard_12": ("standard", 12),
    "pro_1": ("pro", 1),
    "pro_3": ("pro", 3),
    "pro_6": ("pro", 6),
    "pro_12": ("pro", 12),
    "pro_forever": ("pro", -1),
}

# Тарифы с точным числом дней (не календарные месяцы).
# trial_10d (legacy) → squad=basic, для legacy-юзеров.
# trial_standard_10d (new) → squad=standard, для новых юзеров.
# solokhin_15d остаётся на premium (редкий админский промо).
TARIFF_TO_DAYS = {
    "solokhin_15d": ("premium", 15),
    "trial_10d": ("basic", 10),
    "trial_standard_10d": ("standard", 10),
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
    Возвращает remna_user_id (uuid) или None при ошибке/таймауте.

    Логика:
    1. Найти по telegram_id → использовать
    2. Не найден → создать с username по build_remna_username()
    """
    client = RemnaClient()
    try:
        user = await asyncio.wait_for(
            client.get_or_create_user(
                telegram_id=telegram_id,
                tg_username=username,
                tg_first_name=tg_first_name,
                tg_last_name=tg_last_name,
            ),
            timeout=REMNAWAVE_CALL_TIMEOUT,
        )
        return user.uuid
    except asyncio.TimeoutError:
        logger.error(
            f"Remnawave timeout ({REMNAWAVE_CALL_TIMEOUT}s) в ensure_user_in_remnawave "
            f"tg_id={telegram_id}"
        )
        return None
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
    period_days = None
    if tariff in TARIFF_TO_DAYS:
        plan_code, period_days = TARIFF_TO_DAYS[tariff]
        period_months = 0
    else:
        try:
            plan_code, period_months = TARIFF_TO_PLAN.get(
                tariff, TARIFF_TO_PLAN.get(tariff.upper(), ("basic", 1))
            )
        except Exception:
            plan_code, period_months = "basic", 1

    if period_days is not None:
        logger.info(
            f"subscription_provisioning_started: tg_id={telegram_id} tariff={tariff} "
            f"plan={plan_code} period={period_days}d req_id={req_id}"
        )
    else:
        logger.info(
            f"subscription_provisioning_started: tg_id={telegram_id} tariff={tariff} "
            f"plan={plan_code} period={period_months}m req_id={req_id}"
        )
    try:
        remna_user_id = await ensure_user_in_remnawave(telegram_id)
        if not remna_user_id:
            log_payment_event(
                EVENT_REMNAWAVE_PROVISION_FAILED,
                req_id=req_id,
                tg_id=telegram_id,
                payload={"error": "ensure_user_failed"},
            )
            logger.error(f"subscription_provisioning_failed: tg_id={telegram_id} reason=ensure_user_failed")
            return False

        # Определяем valid_until
        if period_months < 0:
            valid_until_str = LIFETIME_EXPIRE_AT
        else:
            now = datetime.now(timezone.utc)
            base = now
            # Продление от текущего expireAt если ещё активна
            try:
                user_data = await asyncio.wait_for(
                    client.get_user_by_id(remna_user_id),
                    timeout=REMNAWAVE_CALL_TIMEOUT,
                )
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
            if period_days is not None:
                valid_until = base + timedelta(days=period_days)
            else:
                valid_until = base + relativedelta(months=period_months)
            valid_until_str = valid_until.strftime("%Y-%m-%dT%H:%M:%SZ")

        from app.core.plans import get_plan_device_limit, get_plan_squad

        device_limit = get_plan_device_limit(plan_code)
        await asyncio.wait_for(
            client.update_user(
                remna_user_id,
                expire_at=valid_until_str,
                hwid_device_limit=device_limit,
            ),
            timeout=REMNAWAVE_CALL_TIMEOUT,
        )

        # Fallback "basic" сохраняем — это бывший дефолт, не ломает legacy.
        squad_name = get_plan_squad(plan_code) or "basic"
        squad = await asyncio.wait_for(
            client.get_squad_by_name(squad_name),
            timeout=REMNAWAVE_CALL_TIMEOUT,
        )
        if not squad or not squad.get("uuid"):
            # Squad должен существовать — если его нет, подписка активна но без доступа к нодам.
            # Явный raise → caller помечает needs_provisioning, админ получит алерт через логи.
            raise RuntimeError(
                f"squad_not_found: тариф={plan_code} squad_name={squad_name!r} — "
                f"проверьте конфигурацию squad'ов в Remnawave. "
                f"tg_id={telegram_id} remna_user_id={remna_user_id}"
            )
        await asyncio.wait_for(
            client.update_user(remna_user_id, activeInternalSquads=[squad["uuid"]]),
            timeout=REMNAWAVE_CALL_TIMEOUT,
        )

        log_payment_event(
            EVENT_REMNAWAVE_PROVISION_SUCCESS,
            req_id=req_id,
            tg_id=telegram_id,
            payload={"remna_user_id": remna_user_id, "tariff": tariff, "valid_until": valid_until_str},
        )
        logger.info(
            f"subscription_provisioning_success: tg_id={telegram_id} remna_user_id={remna_user_id} "
            f"tariff={tariff} expire_at={valid_until_str}"
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
    except asyncio.TimeoutError:
        logger.error(
            f"subscription_provisioning_failed: tg_id={telegram_id} tariff={tariff} "
            f"err=timeout ({REMNAWAVE_CALL_TIMEOUT}s на отдельный вызов Remnawave)"
        )
        log_payment_event(
            EVENT_REMNAWAVE_PROVISION_FAILED,
            req_id=req_id,
            tg_id=telegram_id,
            payload={"error": "remnawave_timeout", "tariff": tariff},
        )
        return False
    except Exception as e:
        logger.error(f"subscription_provisioning_failed: tg_id={telegram_id} tariff={tariff} err={e}")
        log_payment_event(
            EVENT_REMNAWAVE_PROVISION_FAILED,
            req_id=req_id,
            tg_id=telegram_id,
            payload={"error": str(e)[:500], "tariff": tariff},
        )
        return False
    finally:
        await client.close()
