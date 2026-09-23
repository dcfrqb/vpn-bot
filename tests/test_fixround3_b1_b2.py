"""Фикс-раунд 3 хотфикса 2.1: B1 и B2 из прогона дебаг-бота.

B1: первая оплата нового клиента (telegram_users.remna_user_id = NULL) падала в
failed, хотя панель обновлена: Phase B пишет id в своей сессии, а внешняя
сессия перечитывала объекты из identity map со старыми атрибутами. Плюс id
панели не сохранялся на /start, промо, триале и грантах.
B2: оплаченный срок начинался с now, а не с max(now, текущий expireAt), если id
панели не был записан (триал/промо/грант), и остаток сгорал.

Реальный Postgres: tests/integration/test_b1_b2_real_postgres.py.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dateutil.relativedelta import relativedelta

from app.db.models import Payment as PaymentModel, Subscription, TelegramUser
from app.services.payments import yookassa as yk
from app.services.payments.errors import ProvisioningPendingError
from tests.fakes.remnawave import FakeRemna


class _Res:
    def __init__(self, obj):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


class IdentityMapSession:
    """Фейковая AsyncSession с поведением identity map (expire_on_commit=False).

    select(Model) отдает уже загруженный объект КАК ЕСТЬ; свежие значения из
    «БД» (self.db) подтягиваются только при execution_options(populate_existing=True),
    как в настоящей SQLAlchemy.
    """

    def __init__(self, objects):
        self.objects = objects
        self.db = {}
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()
        self.added = []

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, Subscription):
            obj.id = 77
            self.objects[Subscription] = obj

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        obj = self.objects.get(entity)
        if obj is not None and stmt.get_execution_options().get("populate_existing"):
            for k, v in self.db.get(entity, {}).items():
                setattr(obj, k, v)
        return _Res(obj)


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return 1 if key in self.store else 0


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _payment(plan="standard", months=1, amount=249.0):
    return SimpleNamespace(
        id=11, external_id="ext-11", provider="yookassa", amount=amount, currency="RUB",
        subscription_id=None, status="succeeded", paid_at=None,
        payment_metadata={"plan_code": plan, "period_months": months, "expected_amount": amount},
    )


def _sub(**kw):
    base = dict(
        id=77, telegram_user_id=555, plan_code="standard", plan_name="Standard", active=False,
        valid_until=None, provisioning_state="synced", remnawave_expected_expire_at=None,
        remna_user_id=None, config_data={}, last_provisioning_attempt_at=None,
        last_provisioning_error=None, is_lifetime=False,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def _pay(session, fake, sync, amount=249.0):
    bot = AsyncMock()
    with patch.object(yk, "RemnaClient", return_value=fake), \
         patch.object(yk, "get_or_create_remna_user_and_get_subscription_url", side_effect=sync), \
         patch("app.services.cache.get_redis_client", return_value=FakeRedis()), \
         patch.object(yk.settings, "ADMINS", []):
        await yk.handle_successful_payment(
            session=session, payment_id=11, telegram_user_id=555, amount=amount,
            description="x", bot=bot, trace_id="t",
        )
    return bot


def _phase_b(fake, session, sub_holder, remna_id=9):
    """Настоящая Phase B в своей сессии: пишет id и PATCH-ит панель целевой датой."""
    async def _sync(**kwargs):
        sub = sub_holder()
        fake.users[remna_id]["expireAt"] = _iso(sub.remnawave_expected_expire_at)
        fake.users[remna_id]["status"] = "ACTIVE"
        fake.users[remna_id]["activeInternalSquads"] = [
            {"uuid": fake.squads[sub.plan_code], "name": sub.plan_code}
        ]
        session.db[TelegramUser] = {"remna_user_id": str(remna_id)}
        session.db[Subscription] = {"remna_user_id": str(remna_id)}
        return f"https://sub.example/{remna_id}"
    return _sync


# ---------------------------------------------------------------- B1

# test_b1_first_payment_new_customer_synced_on_first_try: removed in 3.0 with the 2.x provisioning (B1/B2 now live in stream B provisioning (tests/panel), max(now, panel, DB) base)


@pytest.mark.asyncio
async def test_b1_stale_read_without_populate_existing_would_fail():
    """Контроль фейка: без перечитывания объект в identity map старый (так падал прод)."""
    tg = SimpleNamespace(telegram_id=555, remna_user_id=None)
    session = IdentityMapSession({TelegramUser: tg})
    session.db[TelegramUser] = {"remna_user_id": "9"}
    from sqlalchemy import select
    plain = (await session.execute(select(TelegramUser))).scalar_one_or_none()
    assert plain.remna_user_id is None
    fresh = (await session.execute(
        select(TelegramUser).execution_options(populate_existing=True)
    )).scalar_one_or_none()
    assert fresh.remna_user_id == "9"


# test_b1_real_failure_still_pending: removed in 3.0 with the 2.x provisioning (B1/B2 now live in stream B provisioning (tests/panel), max(now, panel, DB) base)


@pytest.mark.asyncio
async def test_b1_ensure_user_persists_remna_link():
    """ensure_user_in_remnawave (/start, промо, триал, гранты) пишет id в БД."""
    from app.services import remna_service

    client = MagicMock()
    client.get_or_create_user = AsyncMock(return_value=SimpleNamespace(
        uuid="534", username="tg_555", raw_data={"id": 534, "uuid": "u-534"}))
    client.close = AsyncMock()
    with patch.object(remna_service, "RemnaClient", return_value=client), \
         patch.object(remna_service, "persist_remna_link", new=AsyncMock(return_value=True)) as persist:
        rid = await remna_service.ensure_user_in_remnawave(555)
    assert rid == "534"
    persist.assert_awaited_once()
    args, kwargs = persist.await_args
    assert args == (555, "534")
    assert kwargs["raw_data"] == {"id": 534, "uuid": "u-534"}


@pytest.mark.asyncio
async def test_b1_persist_remna_link_only_fills_null_and_soft_fails():
    from sqlalchemy.dialects import postgresql

    from app.services import remna_service

    executed = []
    session = MagicMock()
    session.execute = AsyncMock(side_effect=lambda stmt: executed.append(stmt))
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("app.db.session.SessionLocal", MagicMock(return_value=cm)):
        assert await remna_service.persist_remna_link(555, 534, username="tg_555") is True
    sql = [str(s.compile(dialect=postgresql.dialect())) for s in executed]
    assert "ON CONFLICT (remna_id) DO NOTHING" in sql[0]
    assert "remna_user_id IS NULL" in sql[1], "существующую привязку не перезаписываем"

    broken = MagicMock(side_effect=RuntimeError("db down"))
    with patch("app.db.session.SessionLocal", broken):
        assert await remna_service.persist_remna_link(555, 534) is False
    assert await remna_service.persist_remna_link(555, None) is False


@pytest.mark.asyncio
async def test_b1_start_persists_link_after_upsert():
    from app.services import users as users_service

    order = []
    session = MagicMock()
    session.execute = AsyncMock(side_effect=lambda stmt: order.append("upsert"))
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    async def _persist(tg_id, rid, **kw):
        order.append(("persist", tg_id, rid))
        return True

    with patch.object(users_service, "ensure_user_in_remnawave", new=AsyncMock(return_value="534")), \
         patch("app.db.session.SessionLocal", MagicMock(return_value=cm)), \
         patch("app.services.remna_service.persist_remna_link", side_effect=_persist):
        user = await users_service.get_or_create_telegram_user(555, first_name="A")
    assert user.remna_user_id == "534"
    assert order == ["upsert", ("persist", 555, "534")], "связь пишется после создания строки telegram_users"


# ---------------------------------------------------------------- B2

# test_b2_trial_then_pay_extends_from_trial_end: removed in 3.0 with the 2.x provisioning (B1/B2 now live in stream B provisioning (tests/panel), max(now, panel, DB) base)


# test_b2_promo_grant_then_pay_extends_from_grant_end: removed in 3.0 with the 2.x provisioning (B1/B2 now live in stream B provisioning (tests/panel), max(now, panel, DB) base)


# test_b2_promo_grant_null_id_then_pay_extends_from_grant_end: removed in 3.0 with the 2.x provisioning (B1/B2 now live in stream B provisioning (tests/panel), max(now, panel, DB) base)


# test_b2_active_paid_renewal_extends_from_current_end: removed in 3.0 with the 2.x provisioning (B1/B2 now live in stream B provisioning (tests/panel), max(now, panel, DB) base)


# test_b2_active_paid_renewal_panel_unreadable_uses_db_valid_until: removed in 3.0 with the 2.x provisioning (B1/B2 now live in stream B provisioning (tests/panel), max(now, panel, DB) base)


# test_b2_expired_user_starts_from_now: removed in 3.0 with the 2.x provisioning (B1/B2 now live in stream B provisioning (tests/panel), max(now, panel, DB) base)
