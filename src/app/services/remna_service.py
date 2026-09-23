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


# Жесткий timeout на отдельный Remnawave-вызов в provision-пути.
# Внутренние retry клиента могут съесть до 90с; webhook-хендлер не должен так висеть.
# При превышении — asyncio.TimeoutError, caller помечает payment.needs_provisioning=True,
# recovery task дожмет асинхронно.
REMNAWAVE_CALL_TIMEOUT = 20.0
# Ревью N6: после таймаута PATCH может дойти до панели чуть позже. Перед
# проверкой «выдача состоялась?» ждем немного, чтобы поздний PATCH успел лечь.
GRANT_RECHECK_DELAY_SECONDS = 3.0
_DISABLED_GRANT_ALERT_TTL = 6 * 3600


# Маппинг тарифов на plan_code и период (в месяцах).
#
# LEGACY (basic/premium) сохраняем как было — продление старых юзеров идет
# через эти же ключи с теми же squad-именами в Remnawave.
#
# NEW (lite/standard/pro) — новая когорта; squads тех же имен должны
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
# Триал с 23.09.2026 = 5 дней (было 10): trial_standard_5d (standard) для /trial,
# trial_5d (basic) для legacy. Старые ключи *_10d оставлены как алиасы на 5 дней,
# чтобы любой старый вызов не выдал 10 дней.
# solokhin_15d остается на premium (редкий админский промо).
TRIAL_DAYS = 5
TARIFF_TO_DAYS = {
    "solokhin_15d": ("premium", 15),
    "trial_5d": ("basic", TRIAL_DAYS),
    "trial_standard_5d": ("standard", TRIAL_DAYS),
    "trial_10d": ("basic", TRIAL_DAYS),
    "trial_standard_10d": ("standard", TRIAL_DAYS),
    "sun718_5d": ("pro", 5),
}


async def ensure_user_in_remnawave(
    telegram_id: int,
    username: Optional[str] = None,
    name: Optional[str] = None,
    tg_first_name: Optional[str] = None,
    tg_last_name: Optional[str] = None,
) -> Optional[str]:
    """
    Получает или создает пользователя в Remnawave.
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


async def _grant_landed(client, remna_user_id: str, valid_until_str: str, plan_code: str) -> bool:
    """True, если в Remnawave уже стоит expireAt не раньше цели (минус 5 минут),
    сквад тарифа на месте и юзер не DISABLED. Любая ошибка чтения -> False."""
    try:
        target = datetime.fromisoformat(valid_until_str.replace("Z", "+00:00"))
        data = await asyncio.wait_for(client.get_user_by_id(str(remna_user_id)), timeout=REMNAWAVE_CALL_TIMEOUT)
        raw = data.get("response", data) if isinstance(data, dict) else {}
        if not isinstance(raw, dict) or str(raw.get("status") or "").upper() == "DISABLED":
            return False
        actual_raw = raw.get("expireAt")
        if not actual_raw:
            return False
        actual = datetime.fromisoformat(str(actual_raw).replace("Z", "+00:00"))
        if actual.tzinfo is None:
            actual = actual.replace(tzinfo=timezone.utc)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        if actual < target - timedelta(minutes=5):
            return False
        from app.core.plans import get_plan_squad
        from app.services.remna_tariff import extract_squad_uuids
        squad_name = get_plan_squad(plan_code)
        squads = await asyncio.wait_for(client.list_internal_squads(), timeout=REMNAWAVE_CALL_TIMEOUT)
        target_uuid = next((sq.get("uuid") for sq in squads if isinstance(sq, dict) and sq.get("name") == squad_name), None)
        return bool(target_uuid) and target_uuid in extract_squad_uuids(raw)
    except Exception as e:
        logger.debug(f"_grant_landed check failed for {remna_user_id}: {e}")
        return False


def _is_timeout_error(exc: BaseException) -> bool:
    """Таймаут где-то в цепочке причин (asyncio / httpx)."""
    seen = 0
    while exc is not None and seen < 5:
        if isinstance(exc, asyncio.TimeoutError) or type(exc).__name__.endswith("Timeout") \
                or type(exc).__name__.endswith("TimeoutException"):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


async def _alert_grant_to_disabled_user(telegram_id: int, tariff: str, req_id: Optional[str]) -> None:
    """Один алерт админам (Redis SET NX, 6 ч): выдача юзеру, отключенному вручную (ревью N2)."""
    from app.services.redis_flags import set_once

    first = await set_once(f"alert:grant_disabled_user:{int(telegram_id)}", str(req_id or "1"),
                           ttl=_DISABLED_GRANT_ALERT_TTL)
    if first is False:
        return
    from html import escape as _he
    from app.services.blocklist import notify_admins

    await notify_admins(
        "⚠️ <b>Выдача не выполнена: пользователь отключен вручную</b>\n\n"
        f"Telegram ID: <code>{int(telegram_id)}</code>\n"
        f"Тариф: {_he(str(tariff))}\n"
        f"Источник: <code>{_he(str(req_id or '—'))}</code>\n\n"
        "Юзер в статусе DISABLED в панели, бот его сам не включает. "
        "Если отключение больше не нужно, включите юзера в панели и повторите выдачу."
    )


async def provision_tariff(
    telegram_id: int,
    tariff: str,
    req_id: Optional[str] = None,
) -> bool:
    """
    Выдает доступ пользователю в Remnawave по тарифу.
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
            # Продление от текущего expireAt если еще активна
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

        # expireAt + сквад тарифа + лимит устройств одним PATCH, без затирания
        # ручных сквадов и поднятых лимитов (services/remna_tariff). Сквад не
        # найден / PATCH упал -> RemnaTariffError -> выдача не удалась (False).
        from app.core.plans import get_plan_squad
        from app.services.remna_tariff import RemnaUserDisabledError, apply_tariff_to_remna_user

        if not get_plan_squad(plan_code):
            plan_code = "basic"  # бывший дефолт, не ломает legacy
        try:
            await asyncio.wait_for(
                apply_tariff_to_remna_user(
                    client, remna_user_id, plan_code, expire_at=valid_until_str, trace_id=req_id,
                    # Ревью N2: промо и админ-гранты не включают юзера,
                    # отключенного в панели вручную.
                    refuse_if_disabled=True,
                ),
                timeout=REMNAWAVE_CALL_TIMEOUT * 2,
            )
        except RemnaUserDisabledError as disabled_err:
            logger.warning(
                f"subscription_provisioning_refused: tg_id={telegram_id} tariff={tariff} "
                f"req_id={req_id} err={disabled_err}"
            )
            log_payment_event(
                EVENT_REMNAWAVE_PROVISION_FAILED,
                req_id=req_id,
                tg_id=telegram_id,
                payload={"error": "remna_user_disabled", "tariff": tariff},
            )
            await _alert_grant_to_disabled_user(telegram_id, tariff, req_id)
            return False
        except Exception as apply_err:
            # Ревью m3/m-3: PATCH мог дойти до панели, а ответ — нет (таймаут).
            # Тогда промо-запись удалялась, и /trial можно было взять еще раз
            # поверх уже продленного срока. Перечитываем юзера: если срок уже
            # стоит, выдача состоялась. Ревью N6: при таймауте сначала ждем,
            # чтобы запоздавший PATCH успел лечь (окно сужено, не закрыто:
            # PATCH, пришедший позже задержки, все еще дает повторный /trial).
            if _is_timeout_error(apply_err) and GRANT_RECHECK_DELAY_SECONDS > 0:
                await asyncio.sleep(GRANT_RECHECK_DELAY_SECONDS)
            if not await _grant_landed(client, remna_user_id, valid_until_str, plan_code):
                raise
            logger.warning(
                f"provision_tariff: apply reported {type(apply_err).__name__} but grant landed "
                f"in Remnawave, treating as success tg_id={telegram_id} tariff={tariff}"
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

        # ===== ОБХОД (две подписки): провижн obhod-юзера для Pro =====
        # provision_tariff — legacy-путь (промокоды /sun718, админ/«друг»-выдачи).
        # Обход интегрирован здесь так же, как в DB-backed handle_successful_payment,
        # чтобы ЛЮБАЯ выдача Pro давала обход (модель «у каждого Pro есть обход»).
        # Мягкий fail: ошибка обхода не должна ронять основную выдачу.
        try:
            # valid_until как naive-UTC datetime (как ждет ensure_obhod_for_pro)
            try:
                obhod_valid_until = datetime.strptime(valid_until_str, "%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                obhod_valid_until = datetime.utcnow() + timedelta(days=3650)  # lifetime fallback
            from app.db.session import SessionLocal
            if SessionLocal:
                from app.core.plans import is_obhod_eligible_plan
                async with SessionLocal() as obhod_session:
                    if is_obhod_eligible_plan(plan_code):
                        # Гарантируем строку telegram_users (FK + lookup в
                        # ensure_obhod_for_pro). Legacy-путь ее сам не создает,
                        # в отличие от DB-backed yookassa-пути.
                        from sqlalchemy.dialects.postgresql import insert as _pg_insert
                        from app.db.models import TelegramUser as _TgUser
                        await obhod_session.execute(
                            _pg_insert(_TgUser)
                            .values(telegram_id=telegram_id)
                            .on_conflict_do_nothing(index_elements=["telegram_id"])
                        )
                        from app.services.obhod_service import ensure_obhod_for_pro
                        await ensure_obhod_for_pro(
                            session=obhod_session,
                            telegram_user_id=telegram_id,
                            plan_code=plan_code,
                            valid_until=obhod_valid_until,
                            trace_id=req_id,
                        )
                    else:
                        # Не-Pro: если был обход (даунгрейд) — гасим.
                        from app.services.obhod_service import deactivate_obhod
                        await deactivate_obhod(obhod_session, telegram_id, trace_id=req_id)
                    await obhod_session.commit()
        except Exception as _obhod_e:
            logger.warning(
                f"obhod provision soft-fail (provision_tariff): tg_id={telegram_id} err={_obhod_e}"
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
