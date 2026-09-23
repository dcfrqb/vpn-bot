"""Хотфикс 2.1, п.1: цена только с сервера, сверка суммы в вебхуке.

Без сети: YooKassa и Remnawave не вызываются, БД — фейковая сессия.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram import types

from app.core.plans import get_expected_amount, get_plan_price
from app.services.payments import yookassa as yk


# --------------------------------------------------------------------------
# Разбор callback_data
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# Какие покупки разрешены
# --------------------------------------------------------------------------







def _pay_callback(data: str) -> MagicMock:
    cb = MagicMock(spec=types.CallbackQuery)
    cb.from_user = types.User(id=555, is_bot=False, first_name="A", username="a")
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.bot = AsyncMock()
    return cb






# --------------------------------------------------------------------------
# create_payment сам отказывает в неверной сумме (до вызова YooKassa)
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# Сверка суммы при обработке оплаты
# --------------------------------------------------------------------------

def test_price_mismatch_reason():
    r = yk._price_mismatch_reason
    assert r("pro", 12, 3999.0, "RUB", {}) is None
    assert r("pro", 12, 1.0, "RUB", {}) is not None
    assert r("pro", 12, 3999.0, "USD", {}) is not None
    assert r("obhod_250", 1, 599.0, "RUB", {}) is None
    assert r("obhod_250", 1, 1.0, "RUB", {}) is not None
    # цена поменялась после создания платежа: сверяемся с зафиксированной сервером
    assert r("pro", 1, 399.0, "RUB", {"expected_amount": "399"}) is None
    assert r("unknown", 1, 100.0, "RUB", {}) is not None


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


def _fake_session(payment):
    session = MagicMock()
    session.execute = AsyncMock(return_value=_Result(payment))
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


def _payment(amount, plan_code="pro", period=12, extra_meta=None):
    meta = {"plan_code": plan_code, "period_months": period}
    meta.update(extra_meta or {})
    return SimpleNamespace(
        id=10, external_id="ext-10", provider="yookassa", amount=amount, currency="RUB",
        subscription_id=None, status="succeeded", paid_at=None, payment_metadata=meta,
    )


# test_underpaid_payment_is_held_for_review_not_provisioned: removed in 3.0 with the 2.x provisioning (tests/money/test_fulfillment.py (price gate once))


# test_obhod_package_underpaid_not_applied: removed in 3.0 with the 2.x provisioning (tests/money/test_fulfillment.py (price gate once))


def test_expected_amount_catalog():
    assert get_expected_amount("pro", 12) == 3999
    assert get_expected_amount("pro", None) == 0
    assert get_expected_amount("trial", 1) == 0
    assert get_expected_amount("obhod_500", 1) == 1199


# test_recovery_skips_payments_on_review: moved to tests/money/test_recovery_sweep.py (3.0 Fulfillment)

def test_quote_purchase_is_the_single_rule():
    from app.core.plans import quote_purchase

    assert quote_purchase("pro", 12) == get_plan_price("pro", 12)
    assert quote_purchase("basic", 3) == 0
    assert quote_purchase("basic", 3, last_plan="basic") == 249
    assert quote_purchase("premium", 1, last_plan="basic") == 0
    assert quote_purchase("trial", 1) == 0
    assert quote_purchase("pro", 600) == 0
    # пакет обхода: кнопкой тарифа не продается, экран пакета — по каталогу
    assert quote_purchase("obhod_250", 1) == 0
    assert quote_purchase("obhod_250", 1, allow_obhod_package=True) == get_expected_amount("obhod_250", 1)






