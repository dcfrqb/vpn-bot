"""
2.x помощники Remnawave, которые еще используются: поиск/привязка аккаунта
(ensure_user_in_remnawave, persist_remna_link), safe_remna_raw и таблицы тарифов.
Выдача доступа здесь больше не живет: единственный путь выдачи в 3.0 это
app.services.provisioning (provision_tariff удален, ревью архитектуры M1).
"""
import asyncio
from typing import Optional


from app.remnawave.client import RemnaClient
from app.logger import logger


# Жесткий timeout на отдельный Remnawave-вызов в provision-пути.
# Внутренние retry клиента могут съесть до 90с; webhook-хендлер не должен так висеть.
# При превышении — asyncio.TimeoutError, caller помечает payment.needs_provisioning=True,
# recovery task дожмет асинхронно.
REMNAWAVE_CALL_TIMEOUT = 20.0


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


_SAFE_REMNA_RAW_FIELDS = ("id", "uuid", "username", "telegramId", "status", "expireAt", "createdAt")


def safe_remna_raw(data) -> Optional[dict]:
    """Урезать ответ панели до безопасных полей перед записью в remna_users.raw_data.

    В полном ответе есть vlessUuid, trojanPassword, ssPassword, shortUuid и
    subscriptionUrl: это ключи доступа к VPN, в базе бота им не место.
    Ответ может быть обернут в {"response": {...}}.
    """
    if not isinstance(data, dict):
        return None
    inner = data.get("response", data)
    if not isinstance(inner, dict):
        return None
    return {k: inner[k] for k in _SAFE_REMNA_RAW_FIELDS if k in inner}


async def persist_remna_link(
    telegram_id: int,
    remna_user_id,
    username: Optional[str] = None,
    raw_data=None,
) -> bool:
    """Записать в БД id юзера панели: строка remna_users (FK) и
    telegram_users.remna_user_id, только если там еще NULL (чужую или более
    раннюю привязку не перезаписываем). Uuid и прочие поля панели лежат в
    remna_users.raw_data. Строку telegram_users не создает. Любая ошибка БД
    глушится: выдача и /start от нее не зависят. True, если запись прошла.

    Фикс B1: раньше id писал только путь оплаты, у нового клиента он был NULL,
    и первая оплата помечалась failed.
    """
    if not remna_user_id:
        return False
    try:
        from sqlalchemy import update
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db import session as _db_session
        from app.db.models import RemnaUser as RemnaUserRow, TelegramUser

        session_factory = _db_session.SessionLocal
        if session_factory is None:
            return False
        rid = str(remna_user_id)
        values = {"remna_id": rid, "username": username}
        safe_raw = safe_remna_raw(raw_data)
        if safe_raw:
            values["raw_data"] = safe_raw
        async with session_factory() as session:
            await session.execute(
                pg_insert(RemnaUserRow).values(**values).on_conflict_do_nothing(index_elements=["remna_id"])
            )
            await session.execute(
                update(TelegramUser)
                .where(TelegramUser.telegram_id == int(telegram_id), TelegramUser.remna_user_id.is_(None))
                .values(remna_user_id=rid)
            )
            await session.commit()
        return True
    except Exception as e:
        logger.warning(f"persist_remna_link soft-fail tg_id={telegram_id} remna_id={remna_user_id}: {e}")
        return False


async def ensure_user_in_remnawave(
    telegram_id: int,
    username: Optional[str] = None,
    name: Optional[str] = None,
    tg_first_name: Optional[str] = None,
    tg_last_name: Optional[str] = None,
    create: bool = True,
) -> Optional[str]:
    """
    Получает или создает пользователя в Remnawave.
    Возвращает remna_user_id (uuid) или None при ошибке/таймауте.

    Логика:
    1. Найти по telegram_id → использовать
    2. Не найден → создать с username по build_remna_username()

    create=False (3.0, поток B): только поиск. /start и синк статуса больше
    не создают аккаунт в панели; его создает только выдача (оплата, промо,
    триал, админ-грант). Не найден -> None.
    """
    client = RemnaClient()
    try:
        if create:
            lookup = client.get_or_create_user(
                telegram_id=telegram_id,
                tg_username=username,
                tg_first_name=tg_first_name,
                tg_last_name=tg_last_name,
            )
        else:
            lookup = client.get_user_by_telegram_id(telegram_id, strict=True)
        user = await asyncio.wait_for(lookup, timeout=REMNAWAVE_CALL_TIMEOUT)
        if user is None:
            return None
        # Фикс B1: связь tg -> юзер панели пишем в БД сразу (/start, промо,
        # триал, гранты), а не только при оплате.
        await persist_remna_link(
            telegram_id, user.uuid,
            username=getattr(user, "username", None),
            raw_data=getattr(user, "raw_data", None),
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
