"""
Сервис обхода блокировок (вторая подписка с лимитом трафика).

Архитектура «две подписки»: у клиента максимум ДВА Remnawave-юзера —
  main  — основная подписка (тарифный сквад, без лимита), резолвится по telegramId;
  obhod — отдельный сквад OBHOD_SQUAD_NAME, помесячный кап трафика, БЕЗ telegramId,
          адресуется ТОЛЬКО по сохраненному uuid (Subscription.remna_user_id строки
          sub_kind='obhod').

Обход выдается ТОЛЬКО в тарифе Pro и его срок = срок Pro. Пакеты «Обход +трафик»
поднимают месячный кап на ТОМ ЖЕ obhod-юзере (третья сущность не создается).

Этот модуль НЕ дергается фоновым reconciler'ом главной подписки: obhod-строки
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

    main дает tg_<...>; добавляем суффикс _obhod, чтобы не коллидировать с
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


async def get_obhod_subscription(
    session, telegram_user_id: int
) -> Optional[Subscription]:
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
    """Создает/обновляет обходного юзера и его подписку для активного Pro.

    Вызывается в Phase C handle_successful_payment ПОСЛЕ синка основной подписки,
    если plan_code дает обход (Pro). Идемпотентна: при повторном вызове
    переиспользует существующего obhod-юзера по сохраненному uuid.

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
            # Кап: если ранее пакетом подняли выше базового и пакет еще активен —
            # оставляем поднятый; иначе ставим базовый.
            limit_bytes = base_limit
            pkg = (obhod_sub.config_data or {}).get("package") if obhod_sub else None
            pkg_until_raw = (
                (obhod_sub.config_data or {}).get("package_until")
                if obhod_sub
                else None
            )
            if pkg and pkg_until_raw:
                try:
                    pkg_until = datetime.fromisoformat(pkg_until_raw)
                    if pkg_until > datetime.utcnow():
                        pkg_limit = get_obhod_package_limit_bytes(pkg)
                        if pkg_limit:
                            limit_bytes = pkg_limit
                except Exception:
                    pass
            # Снимаем DISABLED, только если он реально стоит — иначе панель вернет
            # 400 «User already enabled» и в логах будет лишний ERROR на каждом продлении.
            try:
                _info = await client.get_user_traffic_info(obhod_uuid)
                if (_info or {}).get("status") == "DISABLED":
                    await client.enable_user(obhod_uuid)
            except Exception as _en_e:
                logger.debug(f"[{trace_id}] obhod enable check soft-fail: {_en_e}")
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
            # Создаем нового obhod-юзера БЕЗ telegramId.
            username = build_obhod_username(
                telegram_id=telegram_user_id,
                username=tg.username,
                first_name=tg.first_name,
                last_name=tg.last_name,
            )

            # M1 recovery: username детерминирован. Если предыдущая попытка создала
            # юзера в Remnawave, но DB-строка не записалась (сбой commit → rollback),
            # повторный create уперся бы в duplicate-username и обход не завелся бы
            # никогда. Поэтому СНАЧАЛА пробуем до-резолвить uuid по username; если
            # юзер уже есть — переиспользуем его (продлеваем срок/кап), не создаем.
            obhod_uuid = None
            try:
                existing_remote = await client.get_user_by_username(username)
                if existing_remote:
                    obhod_uuid = existing_remote.get("uuid") or existing_remote.get(
                        "id"
                    )
            except Exception as _re:
                logger.debug(
                    f"[{trace_id}] obhod: предрезолв по username {username!r} не дал результата: {_re}"
                )

            if obhod_uuid:
                # Орфан из прошлой попытки — приводим к нужному состоянию.
                obhod_uuid = str(obhod_uuid)
                await client.update_user(
                    obhod_uuid,
                    expire_at=expire_str,
                    activeInternalSquads=[squad_uuid],
                    traffic_limit_bytes=base_limit,
                    traffic_limit_strategy=OBHOD_TRAFFIC_LIMIT_STRATEGY,
                )
                logger.info(
                    f"[{trace_id}] obhod recovered by username: tg_id={telegram_user_id} "
                    f"username={username} uuid={obhod_uuid} expire={expire_str}"
                )
            else:
                # Lazy-import: generate_remna_password живет в yookassa-сервисе
                # (избегаем тяжелого import на уровне модуля + цикла).
                from app.services.payments.yookassa import generate_remna_password

                password = generate_remna_password(length=24)
                try:
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
                except Exception as create_err:
                    # M1: гонка / орфан между предрезолвом и create — duplicate
                    # username. Резолвим uuid существующего юзера, иначе пробрасываем.
                    resolved = None
                    try:
                        existing_remote = await client.get_user_by_username(username)
                        if existing_remote:
                            resolved = existing_remote.get(
                                "uuid"
                            ) or existing_remote.get("id")
                    except Exception:
                        resolved = None
                    if not resolved:
                        raise create_err
                    obhod_uuid = str(resolved)
                    await client.update_user(
                        obhod_uuid,
                        expire_at=expire_str,
                        activeInternalSquads=[squad_uuid],
                        traffic_limit_bytes=base_limit,
                        traffic_limit_strategy=OBHOD_TRAFFIC_LIMIT_STRATEGY,
                    )
                    logger.warning(
                        f"[{trace_id}] obhod create hit duplicate, recovered by username: "
                        f"tg_id={telegram_user_id} username={username} uuid={obhod_uuid}"
                    )

        subscription_url = await client.get_user_subscription_url(obhod_uuid)

        # Upsert RemnaUser-записи (FK не на subscription, но держим консистентно с main-путем).
        ru_res = await session.execute(
            select(RemnaUser).where(RemnaUser.remna_id == str(obhod_uuid))
        )
        if not ru_res.scalar_one_or_none():
            session.add(RemnaUser(remna_id=str(obhod_uuid), username=None))

        cfg = (
            dict(obhod_sub.config_data) if (obhod_sub and obhod_sub.config_data) else {}
        )
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
    счетчик трафика). Возвращает True если что-то поменяли.
    """
    obhod_sub = await get_obhod_subscription(session, telegram_user_id)
    # Защита: действуем строго на obhod-строке (sub_kind='obhod') и только если активна.
    if (
        not obhod_sub
        or getattr(obhod_sub, "sub_kind", None) != "obhod"
        or not obhod_sub.active
    ):
        return False

    if obhod_sub.remna_user_id:
        client = RemnaClient()
        try:
            # Disable-экшен Remnawave (status=DISABLED). НЕ ставим expireAt в прошлое:
            # панель 2.8.0 отклоняет past expireAt с 400. Disable отзывает доступ,
            # сохраняя uuid/счетчик; возобновление Pro делает enable в ensure_obhod_for_pro.
            await client.disable_user(obhod_sub.remna_user_id)
        except Exception as e:
            logger.warning(
                f"[{trace_id}] obhod deactivate: не смогли отключить uuid="
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
    payment_id: Optional[int] = None,
) -> bool:
    """Поднимает месячный кап обхода до уровня пакета на оплаченный период.

    Требует активного обхода (значит активного Pro). Поднимает trafficLimitBytes
    на ТОМ ЖЕ obhod-юзере. Срок действия пакета пишем в config_data['package_until'];
    по истечении ensure_obhod_for_pro/синк откатит кап к базовому.

    Идемпотентность по платежу (C1): если payment_id уже зафиксирован в
    config_data['applied_payment_id'], повторный вызов — no-op (возвращает True),
    кап/период НЕ поднимаются второй раз. Это защищает от дубль-доставки вебхука
    payment.succeeded (ретраи ЮKassa при 5xx), т.к. на этой ветке нет общего гейта
    already_synced (payment.subscription_id зануляется).

    Возвращает True при успехе (в т.ч. при идемпотентном повторе).
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
            f"— пакет не применен (нужен активный Pro)"
        )
        return False

    # C1: идемпотентность по конкретному платежу. Если этот payment_id уже применен —
    # ничего не делаем (не дергаем Remnawave, не двигаем package_until).
    if payment_id is not None:
        applied_id = (obhod_sub.config_data or {}).get("applied_payment_id")
        if applied_id is not None and str(applied_id) == str(payment_id):
            logger.info(
                f"[{trace_id}] obhod package: payment_id={payment_id} уже применен "
                f"(идемпотентный повтор) — no-op tg_id={telegram_user_id}"
            )
            return True

    package_until = datetime.utcnow() + relativedelta(months=period_months)

    # M2: прежний кап (на случай отката при сбое commit). Если уже стоял активный
    # пакет — его лимит, иначе базовые 100 ГБ. Так split-state (кап поднят в
    # Remnawave, но БД не записала package_until) не оставит юзера с поднятым капом
    # без срока — при сбое commit мы вернем кап к прежнему значению.
    prev_limit = obhod_base_limit_bytes()
    prev_cfg = obhod_sub.config_data or {}
    prev_pkg = prev_cfg.get("package")
    prev_until_raw = prev_cfg.get("package_until")
    if prev_pkg and prev_until_raw:
        try:
            if datetime.fromisoformat(prev_until_raw) > datetime.utcnow():
                _pl = get_obhod_package_limit_bytes(prev_pkg)
                if _pl:
                    prev_limit = _pl
        except Exception:
            pass

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
        await client.close()
        return False

    cfg = dict(obhod_sub.config_data or {})
    cfg["package"] = package_code
    cfg["package_until"] = package_until.isoformat()
    cfg["package_limit_bytes"] = limit_bytes
    if payment_id is not None:
        cfg["applied_payment_id"] = payment_id
    obhod_sub.config_data = cfg
    try:
        await session.commit()
    except Exception as commit_err:
        # M2: БД не записала состояние пакета, а кап в Remnawave уже поднят.
        # Откатываем кап обратно к прежнему значению, чтобы не оставить
        # неоплаченно-расширенный кап без package_until (который при синке Pro
        # все равно сбросится к базовому, но до синка юзер бы пользовался лишним
        # трафиком). Безопаснее вернуть как было и дать платежу пере-провизиниться.
        logger.error(
            f"[{trace_id}] obhod package: commit упал, откатываем кап в Remnawave "
            f"uuid={obhod_sub.remna_user_id} к {prev_limit} err={commit_err}"
        )
        try:
            await session.rollback()
        except Exception:
            pass
        try:
            await client.update_user(
                obhod_sub.remna_user_id,
                traffic_limit_bytes=prev_limit,
                traffic_limit_strategy=OBHOD_TRAFFIC_LIMIT_STRATEGY,
            )
        except Exception as restore_err:
            logger.error(
                f"[{trace_id}] obhod package: НЕ смогли откатить кап после сбоя commit "
                f"uuid={obhod_sub.remna_user_id} err={restore_err}"
            )
        finally:
            await client.close()
        return False

    await client.close()
    logger.info(
        f"[{trace_id}] obhod package applied: tg_id={telegram_user_id} "
        f"package={package_code} limit_bytes={limit_bytes} until={package_until.isoformat()}"
    )
    return True


async def has_active_obhod(telegram_user_id: int) -> bool:
    """True, если у юзера есть АКТИВНАЯ obhod-подписка (значит активный Pro).

    H1: гейт на покупку пакета обхода. Пакет поднимает кап на существующем
    obhod-юзере и применим только при активном обходе; без него apply_obhod_package
    вернет False, а платеж уже succeeded — деньги «в никуда». Проверяем ДО создания
    платежа.

    Открывает свою сессию (вызывается из UI-хендлера, где сессии нет). При
    недоступной БД (SessionLocal is None) возвращает False — безопасный отказ.
    """
    from app.db.session import SessionLocal

    if SessionLocal is None:
        return False

    async with SessionLocal() as session:
        obhod_sub = await get_obhod_subscription(session, telegram_user_id)
        return bool(obhod_sub and obhod_sub.active)


async def get_obhod_link_info(telegram_user_id: int) -> Optional[dict]:
    """Для UI экрана connect: данные обхода у Pro-юзера.

    Возвращает dict {url, used_bytes, limit_bytes, expire_at, active} или None,
    если обхода нет. Лимит/остаток читаем live из Remnawave по сохраненному uuid.
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
