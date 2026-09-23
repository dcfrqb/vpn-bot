"""Профиль пользователя для кабинета сайта (внутренний API, см. api/internal_site.py).

Сайт сам читает из панели Remnawave все живое: ссылку, трафик, устройства,
ноды. Бот отдает только то, чего панель не знает: какие юзеры панели
принадлежат telegram id (obhod-юзер создается без telegramId), тариф и платежи.

Никогда не отдаем ссылки подписки, токены, IP, remna_users.raw_data и чужие
данные: в ответ попадают только поля, перечисленные в build_profile.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from app.core.plans import (
    LEGACY_PLAN_CODES,
    get_obhod_package,
    get_plan_device_limit,
    get_plan_name,
    is_valid_plan_code,
)
from app.logger import logger

PROFILE_CACHE_TTL_SEC = 60
PROFILE_CACHE_PREFIX = "site:profile:"
PAYMENTS_LIMIT = 100
# Поиск юзера панели по telegramId, если в БД нет связки (триал/промо только в
# панели). Короткий таймаут без ретраев: не нашли, значит сайт найдет сам.
PANEL_LOOKUP_TIMEOUT_SEC = 3.0

OBHOD_PLAN_CODE = "pro"      # обход выдается только в Pro
OBHOD_PLAN_TITLE = "RU-вход"


class ProfileDbUnavailable(Exception):
    """БД недоступна: профиль без нее не собрать."""


@dataclass
class DbUser:
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    created_at: Optional[datetime]
    remna_user_id: Optional[str]


@dataclass
class DbSubscription:
    id: int
    sub_kind: str
    plan_code: Optional[str]
    remna_user_id: Optional[str]
    active: bool
    updated_at: Optional[datetime]
    config_data: dict = field(default_factory=dict)


@dataclass
class DbPayment:
    id: int
    provider: Optional[str]
    status: Optional[str]
    amount: Any
    created_at: Optional[datetime]
    paid_at: Optional[datetime]
    payment_metadata: dict = field(default_factory=dict)


@dataclass
class DbSnapshot:
    user: Optional[DbUser]
    subscriptions: list[DbSubscription]
    payments: list[DbPayment]


# ---------------------------------------------------------------------------
# БД
# ---------------------------------------------------------------------------


async def load_db_snapshot(telegram_id: int) -> DbSnapshot:
    """Строки юзера из telegram_users, subscriptions, payments (одна сессия).

    Платежи читаются все: по ним же считаются stats, а в ответ идут 100 новых.
    """
    from sqlalchemy import select

    from app.db import session as db_session
    from app.db.models import Payment, Subscription, TelegramUser

    if db_session.SessionLocal is None:
        raise ProfileDbUnavailable("DATABASE_URL не задан")

    try:
        async with db_session.SessionLocal() as session:
            tg = (
                await session.execute(
                    select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
                )
            ).scalar_one_or_none()
            subs = (
                await session.execute(
                    select(Subscription).where(Subscription.telegram_user_id == telegram_id)
                )
            ).scalars().all()
            pays = (
                await session.execute(
                    select(Payment).where(Payment.telegram_user_id == telegram_id)
                )
            ).scalars().all()
    except Exception as e:
        raise ProfileDbUnavailable(str(e)) from e

    user = None
    if tg is not None:
        user = DbUser(
            telegram_id=int(tg.telegram_id),
            username=tg.username,
            first_name=tg.first_name,
            created_at=tg.created_at,
            remna_user_id=tg.remna_user_id,
        )
    return DbSnapshot(
        user=user,
        subscriptions=[
            DbSubscription(
                id=int(s.id),
                sub_kind=s.sub_kind or "main",
                plan_code=s.plan_code,
                remna_user_id=s.remna_user_id,
                active=bool(s.active),
                updated_at=s.updated_at,
                config_data=dict(s.config_data) if isinstance(s.config_data, dict) else {},
            )
            for s in subs
        ],
        payments=[
            DbPayment(
                id=int(p.id),
                provider=p.provider,
                status=p.status,
                amount=p.amount,
                created_at=p.created_at,
                paid_at=p.paid_at,
                payment_metadata=dict(p.payment_metadata) if isinstance(p.payment_metadata, dict) else {},
            )
            for p in pays
        ],
    )


async def db_is_alive() -> bool:
    """SELECT 1 на Postgres. Для /internal/site/health."""
    try:
        from sqlalchemy import text

        from app.db import session as db_session

        if db_session.SessionLocal is None:
            return False
        async with db_session.SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning(f"site health: db check failed: {type(e).__name__}")
        return False


# ---------------------------------------------------------------------------
# Форматирование
# ---------------------------------------------------------------------------


def iso_utc(value: Any) -> Optional[str]:
    """datetime/ISO-строка -> 'YYYY-MM-DDTHH:MM:SSZ'. Naive считаем UTC
    (в БД бота время пишется через utcnow / now() сервера в UTC)."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _numeric_id(value: Any) -> Optional[int]:
    """Числовой id панели (Remnawave 3.x). None для пустых и legacy-uuid значений
    (2.x-эпоха), чтобы не отдавать их и не падать на int()."""
    if value is None:
        return None
    s = str(value).strip()
    if not s.isdigit():
        return None
    n = int(s)
    return n if n > 0 else None


def _to_rub(value: Any) -> float:
    try:
        return round(float(Decimal(str(value))), 2)
    except Exception:
        return 0.0


def _sort_key_dt(value: Optional[datetime]) -> float:
    if value is None:
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


# ---------------------------------------------------------------------------
# Платежи
# ---------------------------------------------------------------------------


def payment_kind(p: DbPayment) -> str:
    """subscription | obhod_package | promo."""
    if (p.provider or "").lower() == "promo":
        return "promo"
    if get_obhod_package((p.payment_metadata or {}).get("plan_code")) is not None:
        return "obhod_package"
    return "subscription"


def _plan_title(plan_code: Optional[str]) -> Optional[str]:
    return get_plan_name(plan_code) if is_valid_plan_code(plan_code) else None


def payment_description(p: DbPayment, kind: str) -> str:
    """Короткая строка по-русски, без буквы «е с точками»."""
    meta = p.payment_metadata or {}
    months = _to_int(meta.get("period_months"))
    if kind == "promo":
        code = str(meta.get("promo_code") or "").strip()
        head = f"Промокод {code}" if code else "Промокод"
        try:
            from app.services.remna_service import TARIFF_TO_DAYS

            plan, days = TARIFF_TO_DAYS.get(str(meta.get("tariff") or ""), (None, None))
        except Exception:
            plan, days = None, None
        title = _plan_title(plan)
        if title and days:
            return f"{head}: {title}, {days} дн"
        return head
    if kind == "obhod_package":
        pkg = get_obhod_package(meta.get("plan_code")) or {}
        period = months or _to_int(pkg.get("period_months")) or 1
        return f"{OBHOD_PLAN_TITLE}: пакет {pkg.get('limit_gb')} ГБ, {period} мес"
    title = _plan_title(meta.get("plan_code"))
    if title and months:
        return f"{title}, {months} мес"
    return title or "Оплата подписки"


def build_payments(payments: list[DbPayment]) -> list[dict]:
    """Все статусы, новые первыми (created_at, затем id), не больше 100."""
    ordered = sorted(
        payments, key=lambda p: (_sort_key_dt(p.created_at), p.id), reverse=True
    )[:PAYMENTS_LIMIT]
    out = []
    for p in ordered:
        meta = p.payment_metadata or {}
        kind = payment_kind(p)
        plan_code = meta.get("plan_code")
        out.append({
            "id": p.id,
            "created_at": iso_utc(p.created_at),
            "paid_at": iso_utc(p.paid_at),
            "provider": p.provider,
            "status": p.status,
            "amount_rub": _to_rub(p.amount),
            "plan_code": str(plan_code) if plan_code else None,
            "period_months": _to_int(meta.get("period_months")),
            "kind": kind,
            "description": payment_description(p, kind),
        })
    return out


def build_stats(payments: list[DbPayment]) -> dict:
    """Только succeeded и не промо. Дата платежа = paid_at, иначе created_at."""
    paid = [
        p for p in payments
        if p.status == "succeeded" and (p.provider or "").lower() != "promo"
    ]
    moments = [p.paid_at or p.created_at for p in paid if (p.paid_at or p.created_at)]
    moments.sort(key=_sort_key_dt)
    return {
        "payments_count": len(paid),
        "paid_total_rub": round(sum(_to_rub(p.amount) for p in paid), 2),
        "first_payment_at": iso_utc(moments[0]) if moments else None,
        "last_payment_at": iso_utc(moments[-1]) if moments else None,
    }


# ---------------------------------------------------------------------------
# Аккаунты (юзеры панели)
# ---------------------------------------------------------------------------


def _pick_row(subs: list[DbSubscription], kind: str) -> Optional[DbSubscription]:
    """Активная строка вида kind, иначе самая свежая."""
    rows = [s for s in subs if s.sub_kind == kind]
    if not rows:
        return None
    rows.sort(key=lambda s: (s.active, _sort_key_dt(s.updated_at), s.id), reverse=True)
    return rows[0]


def build_obhod_package(row: DbSubscription) -> Optional[dict]:
    cfg = row.config_data or {}
    code = cfg.get("package")
    if not code:
        return None
    limit = _to_int(cfg.get("package_limit_bytes"))
    if limit is None:
        from app.core.plans import get_obhod_package_limit_bytes

        limit = get_obhod_package_limit_bytes(code)
    return {"code": str(code), "until": iso_utc(cfg.get("package_until")), "limit_bytes": limit}


async def _lookup_panel_main_id(telegram_id: int) -> tuple[Optional[int], bool]:
    """(numeric id юзера панели по telegramId, удалось ли спросить панель).

    Существующий клиент бота, strict=True: «нет юзера» и «панель лежит»
    различаются. Только чтение, ничего не создаем.
    """
    from app.remnawave.client import RemnaClient

    client = RemnaClient(max_retries=0)
    try:
        remna_user = await asyncio.wait_for(
            client.get_user_by_telegram_id(telegram_id, strict=True),
            timeout=PANEL_LOOKUP_TIMEOUT_SEC,
        )
        found = _numeric_id(remna_user.uuid) if remna_user else None
        return found, True
    except Exception as e:
        logger.warning(f"site profile: поиск в панели не удался tg_id={telegram_id}: {type(e).__name__}")
        return None, False
    finally:
        await client.close()


async def build_accounts(telegram_id: int, snap: DbSnapshot) -> tuple[list[dict], bool]:
    """(accounts, complete). complete=False, если панель не ответила на поиск."""
    from app.services.users import get_user_last_plan

    accounts: list[dict] = []
    complete = True

    main_row = _pick_row(snap.subscriptions, "main")
    main_id = _numeric_id(snap.user.remna_user_id if snap.user else None) or _numeric_id(
        main_row.remna_user_id if main_row else None
    )
    if not main_id:
        # Значение в БД пустое или не числовое (legacy uuid эпохи 2.x) — считаем,
        # что связки нет, и пробуем найти юзера в панели по telegramId.
        main_id, complete = await _lookup_panel_main_id(telegram_id)

    if main_id:
        plan_code = main_row.plan_code if main_row and main_row.plan_code else None
        if not plan_code:
            plan_code = await get_user_last_plan(telegram_id)
        accounts.append({
            "kind": "main",
            "remna_id": main_id,
            "plan_code": plan_code,
            "plan_title": _plan_title(plan_code),
            "legacy": plan_code in LEGACY_PLAN_CODES,
            "device_limit": get_plan_device_limit(plan_code) if is_valid_plan_code(plan_code) else None,
        })

    obhod_row = _pick_row(snap.subscriptions, "obhod")
    obhod_id = _numeric_id(obhod_row.remna_user_id) if obhod_row else None
    if obhod_row and obhod_id:
        accounts.append({
            "kind": "obhod",
            "remna_id": obhod_id,
            "plan_code": OBHOD_PLAN_CODE,
            "plan_title": OBHOD_PLAN_TITLE,
            "legacy": False,
            "device_limit": get_plan_device_limit(OBHOD_PLAN_CODE),
            "package": build_obhod_package(obhod_row),
        })
    return accounts, complete


# ---------------------------------------------------------------------------
# Профиль целиком + кэш
# ---------------------------------------------------------------------------


async def build_profile(telegram_id: int) -> tuple[Optional[dict], bool]:
    """(профиль или None, если юзера нет; можно ли класть в кэш).

    404: нет строки telegram_users и нет ни одной подписки.
    """
    snap = await load_db_snapshot(telegram_id)
    if snap.user is None and not snap.subscriptions:
        return None, False

    accounts, complete = await build_accounts(telegram_id, snap)
    user = snap.user
    profile = {
        "user": {
            "telegram_id": telegram_id,
            "username": user.username if user else None,
            "first_name": user.first_name if user else None,
            "customer_since": iso_utc(user.created_at) if user else None,
        },
        "accounts": accounts,
        "payments": build_payments(snap.payments),
        "stats": build_stats(snap.payments),
    }
    return profile, complete


def _cache_key(telegram_id: int) -> str:
    return f"{PROFILE_CACHE_PREFIX}{int(telegram_id)}"


async def _cache_get(telegram_id: int) -> Optional[dict]:
    try:
        from app.services.cache import get_redis_client

        client = get_redis_client()
        if client is None:
            return None
        raw = await client.get(_cache_key(telegram_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception as e:
        logger.debug(f"site profile cache get soft-fail: {type(e).__name__}")
        return None


async def _cache_set(telegram_id: int, profile: dict) -> None:
    try:
        from app.services.cache import get_redis_client

        client = get_redis_client()
        if client is None:
            return
        await client.set(
            _cache_key(telegram_id),
            json.dumps(profile, ensure_ascii=False).encode("utf-8"),
            ex=PROFILE_CACHE_TTL_SEC,
        )
    except Exception as e:
        logger.debug(f"site profile cache set soft-fail: {type(e).__name__}")


async def get_profile(telegram_id: int) -> Optional[dict]:
    """Профиль из кэша (60 с) или собранный заново. None = юзера нет (404).

    Бросает ProfileDbUnavailable, если БД недоступна. Результат без ответа
    панели на поиск main-юзера в кэш не кладем, чтобы не держать неполный
    список аккаунтов минуту.
    """
    cached = await _cache_get(telegram_id)
    if cached is not None:
        return cached
    profile, cacheable = await build_profile(telegram_id)
    if profile is not None and cacheable:
        await _cache_set(telegram_id, profile)
    return profile
