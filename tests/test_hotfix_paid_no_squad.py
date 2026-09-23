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


# test_verify_requires_plan_squad: removed in 3.0 with the 2.x provisioning (verify of the plan squad is stream B, tests/panel)


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


# test_paid_but_squad_missing_is_failed_and_alerted_once: removed in 3.0 with the 2.x provisioning (verify of the plan squad is stream B, tests/panel)


# test_new_user_created_without_squad_is_not_silently_ok: removed in 3.0 with the 2.x provisioning (verify of the plan squad is stream B, tests/panel)
