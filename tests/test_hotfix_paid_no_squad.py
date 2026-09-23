"""Хотфикс 2.1, п.8: «оплачено, но без сквада» не считается выданным.

- verify требует сквад тарифа у юзера;
- выдача с ошибкой сквада -> provisioning_state=failed, ProvisioningPendingError
  (вебхук 503 -> повтор, recovery/реконсилер подхватят), алерт админам один раз.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import Payment as PaymentModel, Subscription, TelegramUser
from app.services.payments import yookassa as yk
from app.services.payments.errors import ProvisioningPendingError
from tests.fakes.remnawave import FakeRemna


class _Res:
    def __init__(self, obj):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


class EntitySession:
    """Фейковая AsyncSession: select(Model) -> объект этой модели из словаря."""

    def __init__(self, objects):
        self.objects = objects
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, stmt):
        entity = stmt.column_descriptions[0].get("entity")
        return _Res(self.objects.get(entity))


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


@pytest.mark.asyncio
async def test_verify_requires_plan_squad():
    fake = FakeRemna()
    fake.add_user(1, "tg_a", telegram_id=1, squads=["pro-friend"], expire="2030-01-01T00:00:00Z")
    fake.add_user(2, "tg_b", telegram_id=2, squads=["lite", "arcadia"], expire="2030-01-01T00:00:00Z")
    with patch.object(yk, "RemnaClient", return_value=fake):
        ok, _, err = await yk._verify_remnawave_synced("1", datetime(2029, 12, 31), "t", plan_code="lite")
        assert not ok and "missing" in err
        ok, _, err = await yk._verify_remnawave_synced("2", datetime(2029, 12, 31), "t", plan_code="lite")
        assert ok, err
        fake.fail_squads = True
        ok, _, err = await yk._verify_remnawave_synced("2", datetime(2029, 12, 31), "t", plan_code="lite")
        assert not ok


def _objects():
    payment = SimpleNamespace(
        id=11, external_id="ext-11", provider="yookassa", amount=129.0, currency="RUB",
        subscription_id=None, status="succeeded", paid_at=None,
        payment_metadata={"plan_code": "lite", "period_months": 1, "expected_amount": 129},
    )
    tg = SimpleNamespace(telegram_id=555, remna_user_id="9", username=None, first_name="A", last_name=None)
    sub = SimpleNamespace(
        id=33, telegram_user_id=555, plan_code="lite", plan_name="Lite", active=True,
        valid_until=datetime.utcnow() + timedelta(days=3), provisioning_state="synced",
        remnawave_expected_expire_at=None, remna_user_id="9", config_data={},
        last_provisioning_attempt_at=None, last_provisioning_error=None,
    )
    return payment, tg, sub


@pytest.mark.asyncio
async def test_paid_but_squad_missing_is_failed_and_alerted_once():
    payment, tg, sub = _objects()
    session = EntitySession({PaymentModel: payment, TelegramUser: tg, Subscription: sub})

    fake = FakeRemna()
    # у юзера нет сквада lite (например, сквад переименовали в панели)
    fake.add_user(9, "tg_a", telegram_id=555, squads=[], expire="2026-10-01T00:00:00Z")
    fake.squads.pop("lite")

    async def _sync_ok(**kwargs):
        # Remnawave «принял» дату, но сквада нет: раньше это становилось synced
        fake.users[9]["expireAt"] = sub.remnawave_expected_expire_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        return "https://sub.example/9"

    bot = AsyncMock()
    redis = FakeRedis()
    with patch.object(yk, "RemnaClient", return_value=fake), \
         patch.object(yk, "get_or_create_remna_user_and_get_subscription_url", side_effect=_sync_ok), \
         patch("app.services.cache.get_redis_client", return_value=redis), \
         patch.object(yk.settings, "ADMINS", [900]):
        with pytest.raises(ProvisioningPendingError):
            await yk.handle_successful_payment(
                session=session, payment_id=11, telegram_user_id=555, amount=129.0,
                description="x", bot=bot, trace_id="t1",
            )
        assert sub.provisioning_state == "failed"
        assert "squad" in (sub.last_provisioning_error or "")
        assert payment.subscription_id is None, "платеж не привязан к выданной подписке"
        admin_msgs = [c for c in bot.send_message.await_args_list if c.kwargs["chat_id"] == 900]
        assert len(admin_msgs) == 1
        user_msgs = [c for c in bot.send_message.await_args_list if c.kwargs["chat_id"] == 555]
        assert user_msgs == [], "юзеру не пишем «активировано», пока нет сквада"

        # повтор (ретрай вебхука) — без второго алерта
        sub.provisioning_state = "failed"
        with pytest.raises(ProvisioningPendingError):
            await yk.handle_successful_payment(
                session=session, payment_id=11, telegram_user_id=555, amount=129.0,
                description="x", bot=bot, trace_id="t2",
            )
        admin_msgs = [c for c in bot.send_message.await_args_list if c.kwargs["chat_id"] == 900]
        assert len(admin_msgs) == 1


@pytest.mark.asyncio
async def test_new_user_created_without_squad_is_not_silently_ok():
    """Создание юзера, когда сквад тарифа не найден: функция не отдает ссылку (-> failed)."""
    tg = SimpleNamespace(telegram_id=556, remna_user_id=None, username="newbie", first_name=None, last_name=None)
    sub = SimpleNamespace(id=34, plan_code="lite", remnawave_expected_expire_at=datetime(2026, 11, 1),
                          valid_until=None, config_data={}, remna_user_id=None)

    class _Seq:
        def __init__(self, items):
            self.items = list(items)

        def __call__(self, stmt):
            return _Res(self.items.pop(0)) if self.items else _Res(None)

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_Seq([tg, sub, None]))
    session.commit = AsyncMock()
    session.add = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    fake = FakeRemna()
    fake.squads.pop("lite")
    with patch.object(yk, "SessionLocal", MagicMock(return_value=cm)), \
         patch.object(yk, "RemnaClient", return_value=fake):
        url = await yk.get_or_create_remna_user_and_get_subscription_url(
            telegram_user_id=556, subscription_id=34, period_months=1,
        )
    assert url is None
    assert len(fake.created) == 1, "юзер создан, но без сквада выдача не засчитана"
    assert fake.users[fake.created[0]["id"]]["activeInternalSquads"] == []
