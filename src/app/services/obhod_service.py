"""
Сервис обхода блокировок (вторая подписка с лимитом трафика).

Архитектура «две подписки»: у клиента максимум ДВА Remnawave-юзера —
  main  — основная подписка (тарифный сквад, без лимита), резолвится по telegramId;
  obhod — отдельный сквад OBHOD_SQUAD_NAME, помесячный кап трафика, БЕЗ telegramId,
          адресуется ТОЛЬКО по сохранённому uuid (Subscription.remna_user_id строки
          sub_kind='obhod').

Обход выдаётся ТОЛЬКО в тарифе Pro и его срок = срок Pro. Пакеты «Обход +трафик»
поднимают месячный кап на ТОМ ЖЕ obhod-юзере (третья сущность не создаётся).

Этот модуль НЕ дёргается фоновым reconciler'ом главной подписки: obhod-строки
исключены из main-резолва (sub_kind='main') и из resync-пути (см. reconciler).
Жизненный цикл обхода привязан к main: обновление/истечение Pro синкает обход.
"""
from datetime import datetime, timezone
from typing import Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy import select

from app.core.plans import (
    OBHOD_SQUAD_NAME,
    OBHOD_TRAFFIC_LIMIT_STRATEGY,
    get_obhod_package_limit_bytes,
    is_obhod_eligible_plan,
    obhod_base_limit_bytes,
)
from app.db.models import RemnaUser, Subscription, TelegramUser
from app.logger import logger
from app.remnawave.client import RemnaClient, normalize_expire_at
from app.utils.remna_username import build_remna_username


OBHOD_PLAN_CODE = "obhod"  # plan_code строки sub_kind='obhod' (не из меню тарифов)


def build_obhod_username(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> str:
    """Уникальный username обходного юзера: <main_username>_obhod.

    main даёт tg_<...>; добавляем суффикс _obhod, чтобы не коллидировать с
    основным юзером в Remnawave.
    """
    base = build_remna_username(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
    )
    return f"{base}_obhod"


async def _get_obhod_squad_uuid(client: RemnaClient) -> Optional[str]:
    squad = await client.get_squad_by_name(OBHOD_SQUAD_NAME)
    if not squad or not squad.get("uuid"):
        return None
    return squad.get("uuid")


async def get_obhod_subscription(session, telegram_user_id: int) -> Optional[Subscription]:
    """Строка подписки обхода (sub_kind='obhod') для юзера, или None."""
    res = await session.execute(
        select(Subscription).where(
            Subscription.telegram_user_id == telegram_user_id,
            Subscription.sub_kind == "obhod",
        )
    )
    return res.scalar_one_or_none()


async def ensure_obhod_for_pro(
    session,
    telegram_user_id: int,
    plan_code: Optional[str],
    valid_until: datetime,
    trace_id: Optional[str] = None,
) -> Optional[str]:
    """Создаёт/обновляет обходного юзера и его подписку для активного Pro.

    Вызывается в Phase C handle_successful_payment ПОСЛЕ синка основной подписки,
    если plan_code даёт обход (Pro). Идемпотентна: при повторном вызове
    переиспользует существующего obhod-юзера по сохранённому uuid.

    valid_until — срок Pro (UTC naive, как в основной подписке). Обходу ставится
    тот же expireAt. Кап трафика НЕ трогаем, если он уже поднят пакетом выше
    базового (см. config_data['package_limit_bytes']).

    Возвращает subscription_url обхода или None при недоступности Remnawave/сквада.
    НЕ бросает исключение наружу — обход не должен ронять основную выдачу Pro.
    """
    if not is_obhod_eligible_plan(plan_code):
        return None

    tg_res = await session.execute(
        select(TelegramUser).where(TelegramUser.telegram_id == telegram_user_id)
    )
    tg = tg_res.scalar_one_or_none()
    if not tg:
        logger.error(f"[{trace_id}] obhod: telegram_user {telegram_user_id} не найден")
        return None

    obhod_sub = await get_obhod_subscription(session, telegram_user_id)

    expire_str = normalize_expire_at(valid_until)
    base_limit = obhod_base_limit_bytes()

    client = RemnaClient()
    try:
        squad_uuid = await _get_obhod_squad_uuid(client)
        if not squad_uuid:
            logger.error(
                f"[{trace_id}] obhod: сквад {OBHOD_SQUAD_NAME!r} не найден в Remnawave — "
                f"обход не выдан tg_id={telegram_user_id}"
            )
            return None

        obhod_uuid = obhod_sub.remna_user_id if obhod_sub else None

        if obhod_uuid:
            # Уже есть obhod-юзер — продлеваем срок и подтверждаем сквад.
            # Кап: если ранее пакетом подняли выше базового и пакет ещё активен —
            # оставляем поднятый; иначе ставим базовый.
            limit_bytes = base_limit
            pkg = (obhod_sub.config_data or {}).get("package") if obhod_sub else None
            pkg_until_raw = (obhod_sub.config_data or {}).get("package_until") if obhod_sub else None
            if pkg and pkg_until_raw:
                try:
                    pkg_until = datetime.fromisoformat(pkg_until_raw)
                    if pkg_until > datetime.utcnow():
                        pkg_limit = get_obhod_package_limit_bytes(pkg)
                        if pkg_limit:
                            limit_bytes = pkg_limit
                except Exception:
                    pass
            await client.update_user(
                obhod_uuid,
                expire_at=expire_str,
                activeInternalSquads=[squad_uuid],
                traffic_limit_bytes=limit_bytes,
                traffic_limit_strategy=OBHOD_TRAFFIC_LIMIT_STRATEGY,
            )
            logger.info(
                f"[{trace_id}] obhod updated: tg_id={telegram_user_id} uuid={obhod_uuid} "
                f"expire={expire_str} limit_bytes={limit_bytes}"
            )
        else:
            # Создаём нового obhod-юзера БЕЗ telegramId.
            username = build_obhod_username(
                telegram_id=telegram_user_id,
                username=tg.username,
                first_name=tg.first_name,
                last_name=tg.last_name,
            )
            # Lazy-import: generate_remna_password живёт в yookassa-сервисе
            # (избегаем тяжёлого import на уровне модуля + цикла).
            from app.services.payments.yookassa import generate_remna_password

            password = generate_remna_password(length=24)
            obhod_uuid = await client.create_obhod_user(
                username=username,
                password=password,
                expire_at=expire_str,
                active_internal_squads=[squad_uuid],
                traffic_limit_bytes=base_limit,
                traffic_limit_strategy=OBHOD_TRAFFIC_LIMIT_STRATEGY,
                display_name=f"obhod {telegram_user_id}",
            )
            logger.info(
                f"[{trace_id}] obhod created: tg_id={telegram_user_id} uuid={obhod_uuid} "
                f"expire={expire_str} limit_bytes={base_limit}"
            )

        subscription_url = await client.get_user_subscription_url(obhod_uuid)

        # Upsert RemnaUser-записи (FK не на subscription, но держим консистентно с main-путём).
        ru_res = await session.execute(
            select(RemnaUser).where(RemnaUser.remna_id == str(obhod_uuid))
        )
        if not ru_res.scalar_one_or_none():
            session.add(RemnaUser(remna_id=str(obhod_uuid), username=None))

        cfg = dict(obhod_sub.config_data) if (obhod_sub and obhod_sub.config_data) else {}
        if subscription_url:
            cfg["subscription_url"] = subscription_url

        if obhod_sub:
            obhod_sub.active = True
            obhod_sub.valid_until = valid_until
            obhod_sub.remna_user_id = str(obhod_uuid)
            obhod_sub.provisioning_state = "synced"
            obhod_sub.remnawave_synced_at = datetime.utcnow()
            obhod_sub.remnawave_expected_expire_at = valid_until
            obhod_sub.config_data = cfg
        else:
            obhod_sub = Subscription(
                telegram_user_id=telegram_user_id,
                remna_user_id=str(obhod_uuid),
                plan_code=OBHOD_PLAN_CODE,
                plan_name="Обход блокировок",
                sub_kind="obhod",
                active=True,
                valid_until=valid_until,
                provisioning_state="synced",
                remnawave_synced_at=datetime.utcnow(),
                remnawave_expected_expire_at=valid_until,
                config_data=cfg,
            )
            session.add(obhod_sub)

        await session.commit()
        return subscription_url
    except Exception as e:
        logger.error(
            f"[{trace_id}] obhod ensure failed (не критично для основной Pro): "
            f"tg_id={telegram_user_id} err={e}"
        )
        try:
            await session.rollback()
        except Exception:
            pass
        return None
    finally:
        await client.close()


async def deactivate_obhod(
    session,
    telegram_user_id: int,
    trace_id: Optional[str] = None,
) -> bool:
    """Гасит обход (истечение/даунгрейд Pro): active=false и EXPIRED в Remnawave.

    Не удаляет obhod-юзера (чтобы при возобновлении Pro переиспользовать uuid и
    счётчик трафика). Возвращает True если что-то поменяли.
    """
    obhod_sub = await get_obhod_subscription(session, telegram_user_id)
    # Защита: действуем строго на obhod-строке (sub_kind='obhod') и только если активна.
    if not obhod_sub or getattr(obhod_sub, "sub_kind", None) != "obhod" or not obhod_sub.active:
        return False

    if obhod_sub.remna_user_id:
        client = RemnaClient()
        try:
            # Ставим expireAt в прошлое → Remnawave переведёт юзера в EXPIRED.
            past = (datetime.now(timezone.utc) - relativedelta(days=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            await client.update_user(obhod_sub.remna_user_id, expire_at=past)
        except Exception as e:
            logger.warning(
                f"[{trace_id}] obhod deactivate: не смогли истечь uuid="
                f"{obhod_sub.remna_user_id} err={e}"
            )
        finally:
            await client.close()

    obhod_sub.active = False
    await session.commit()
    logger.info(f"[{trace_id}] obhod deactivated: tg_id={telegram_user_id}")
    return True


async def apply_obhod_package(
    session,
    telegram_user_id: int,
    package_code: str,
    trace_id: Optional[str] = None,
) -> bool:
    """Поднимает месячный кап обхода до уровня пакета на оплаченный период.

    Требует активного обхода (значит активного Pro). Поднимает trafficLimitBytes
    на ТОМ ЖЕ obhod-юзере. Срок действия пакета пишем в config_data['package_until'];
    по истечении ensure_obhod_for_pro/синк откатит кап к базовому.

    Возвращает True при успехе.
    """
    limit_bytes = get_obhod_package_limit_bytes(package_code)
    if not limit_bytes:
        logger.error(f"[{trace_id}] obhod package: неизвестный пакет {package_code!r}")
        return False

    from app.core.plans import get_obhod_package

    meta = get_obhod_package(package_code)
    period_months = int(meta.get("period_months", 1)) if meta else 1

    obhod_sub = await get_obhod_subscription(session, telegram_user_id)
    if not obhod_sub or not obhod_sub.active or not obhod_sub.remna_user_id:
        logger.warning(
            f"[{trace_id}] obhod package: нет активного обхода у tg_id={telegram_user_id} "
            f"— пакет не применён (нужен активный Pro)"
        )
        return False

    package_until = datetime.utcnow() + relativedelta(months=period_months)

    client = RemnaClient()
    try:
        await client.update_user(
            obhod_sub.remna_user_id,
            traffic_limit_bytes=limit_bytes,
            traffic_limit_strategy=OBHOD_TRAFFIC_LIMIT_STRATEGY,
        )
    except Exception as e:
        logger.error(
            f"[{trace_id}] obhod package: не смогли поднять кап uuid="
            f"{obhod_sub.remna_user_id} err={e}"
        )
        return False
    finally:
        await client.close()

    cfg = dict(obhod_sub.config_data or {})
    cfg["package"] = package_code
    cfg["package_until"] = package_until.isoformat()
    cfg["package_limit_bytes"] = limit_bytes
    obhod_sub.config_data = cfg
    await session.commit()
    logger.info(
        f"[{trace_id}] obhod package applied: tg_id={telegram_user_id} "
        f"package={package_code} limit_bytes={limit_bytes} until={package_until.isoformat()}"
    )
    return True


async def get_obhod_link_info(telegram_user_id: int) -> Optional[dict]:
    """Для UI экрана connect: данные обхода у Pro-юзера.

    Возвращает dict {url, used_bytes, limit_bytes, expire_at, active} или None,
    если обхода нет. Лимит/остаток читаем live из Remnawave по сохранённому uuid.
    """
    from app.db.session import SessionLocal

    if SessionLocal is None:
        return None

    async with SessionLocal() as session:
        obhod_sub = await get_obhod_subscription(session, telegram_user_id)
        if not obhod_sub:
            return None
        remna_uuid = obhod_sub.remna_user_id
        active = bool(obhod_sub.active)
        saved_url = (obhod_sub.config_data or {}).get("subscription_url")
        valid_until = obhod_sub.valid_until

    info = {
        "url": saved_url,
        "used_bytes": None,
        "limit_bytes": None,
        "expire_at": valid_until,
        "active": active,
    }
    if not remna_uuid:
        return info

    client = RemnaClient()
    try:
        traffic = await client.get_user_traffic_info(remna_uuid)
        info["used_bytes"] = traffic.get("used_bytes")
        info["limit_bytes"] = traffic.get("limit_bytes")
        if not info["url"]:
            info["url"] = await client.get_user_subscription_url(remna_uuid)
    except Exception as e:
        logger.debug(f"obhod link info soft-fail tg_id={telegram_user_id}: {e}")
    finally:
        await client.close()
    return info
