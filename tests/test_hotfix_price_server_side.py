"""Хотфикс 2.1, п.1: цена только с сервера, сверка суммы в вебхуке.

Без сети: YooKassa и Remnawave не вызываются, БД — фейковая сессия.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from app.core.plans import get_expected_amount, get_plan_price
from app.legacy.routers import payments as legacy_payments
from app.services.payments import yookassa as yk


# --------------------------------------------------------------------------
# Разбор callback_data
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "data,expected",
    [
        ("pay_yookassa_pro_12", ("pro", 12)),
        ("pay_yookassa_pro_12_1", ("pro", 12)),        # старый формат: сумма игнорируется
        ("pay_yookassa_lite_1_129", ("lite", 1)),
        ("pay_yookassa_basic", ("basic", 1)),           # самый старый формат
        ("pay_yookassa_pro_x", None),
        ("pay_yookassa_pro_0", None),
        ("pay_yookassa_pro_-1", None),
        ("pay_yookassa_", None),
        ("pay_yookassa_pro_1_2_3", None),
    ],
)
def test_parse_pay_callback(data, expected):
    assert legacy_payments.parse_pay_callback(data) == expected


# --------------------------------------------------------------------------
# Какие покупки разрешены
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_menu_plan_priced_from_catalog():
    with patch("app.services.users.get_user_last_plan", AsyncMock(return_value=None)):
        assert await legacy_payments.resolve_purchase_amount("pro", 12, 1) == get_plan_price("pro", 12)
        assert await legacy_payments.resolve_purchase_amount("lite", 1, 1) == 129


@pytest.mark.asyncio
async def test_unsold_period_and_service_plans_rejected():
    with patch("app.services.users.get_user_last_plan", AsyncMock(return_value=None)):
        assert await legacy_payments.resolve_purchase_amount("pro", 600, 1) == 0
        assert await legacy_payments.resolve_purchase_amount("trial", 1, 1) == 0
        assert await legacy_payments.resolve_purchase_amount("obhod_250", 1, 1) == 0
        assert await legacy_payments.resolve_purchase_amount("nope", 1, 1) == 0


@pytest.mark.asyncio
async def test_legacy_plan_only_for_its_owner():
    with patch("app.services.users.get_user_last_plan", AsyncMock(return_value="basic")):
        assert await legacy_payments.resolve_purchase_amount("basic", 3, 1) == 249
        assert await legacy_payments.resolve_purchase_amount("premium", 1, 1) == 0
    with patch("app.services.users.get_user_last_plan", AsyncMock(return_value="lite")):
        assert await legacy_payments.resolve_purchase_amount("basic", 1, 1) == 0


def _pay_callback(data: str) -> MagicMock:
    cb = MagicMock(spec=types.CallbackQuery)
    cb.from_user = types.User(id=555, is_bot=False, first_name="A", username="a")
    cb.data = data
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.bot = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_forged_amount_in_callback_is_ignored():
    cb = _pay_callback("pay_yookassa_pro_12_1")
    create = AsyncMock(return_value=("https://pay.example/x", "ext-1"))
    with patch.object(legacy_payments, "create_payment", create), \
         patch.object(legacy_payments, "try_schedule_autorecheck", AsyncMock(return_value=False)), \
         patch("app.services.users.get_user_last_plan", AsyncMock(return_value=None)):
        await legacy_payments.handle_yookassa_payment(cb)
    create.assert_awaited_once()
    assert create.await_args.kwargs["amount_rub"] == 3999
    assert create.await_args.kwargs["plan_code"] == "pro"
    assert create.await_args.kwargs["period_months"] == 12


@pytest.mark.asyncio
async def test_forged_legacy_plan_not_sold_to_stranger():
    cb = _pay_callback("pay_yookassa_basic_12_1")
    create = AsyncMock()
    with patch.object(legacy_payments, "create_payment", create), \
         patch("app.services.users.get_user_last_plan", AsyncMock(return_value=None)):
        await legacy_payments.handle_yookassa_payment(cb)
    create.assert_not_awaited()
    assert "недоступен" in cb.message.edit_text.await_args.args[0]


# --------------------------------------------------------------------------
# create_payment сам отказывает в неверной сумме (до вызова YooKassa)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_payment_rejects_wrong_amount():
    with patch("app.services.blocklist.get_user_block_reason", AsyncMock(return_value=None)), \
         patch.object(yk, "_create_yookassa_payment", AsyncMock()) as yk_create:
        with pytest.raises(ValueError):
            await yk.create_payment(amount_rub=1, description="x", user_id=1, plan_code="pro", period_months=12)
        with pytest.raises(ValueError):
            await yk.create_payment(amount_rub=0, description="x", user_id=1, plan_code="trial", period_months=1)
        yk_create.assert_not_called()


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


@pytest.mark.asyncio
async def test_underpaid_payment_is_held_for_review_not_provisioned():
    payment = _payment(1.0)
    session = _fake_session(payment)
    bot = AsyncMock()
    remna = AsyncMock()
    with patch.object(yk, "get_or_create_remna_user_and_get_subscription_url", remna), \
         patch.object(yk.settings, "ADMINS", [900]):
        outcome = await yk.handle_successful_payment(
            session=session, payment_id=10, telegram_user_id=555, amount=1.0,
            description="x", bot=bot, trace_id="t",
        )
    assert outcome == "review"
    remna.assert_not_awaited()
    assert payment.payment_metadata["needs_review"] is True
    assert payment.payment_metadata["review_alerted"] is True
    chat_ids = [c.kwargs["chat_id"] for c in bot.send_message.await_args_list]
    assert 900 in chat_ids and 555 in chat_ids

    # повторная обработка (ретрай вебхука/recovery) не спамит алертами
    bot.send_message.reset_mock()
    with patch.object(yk, "get_or_create_remna_user_and_get_subscription_url", remna), \
         patch.object(yk.settings, "ADMINS", [900]):
        outcome = await yk.handle_successful_payment(
            session=session, payment_id=10, telegram_user_id=555, amount=1.0,
            description="x", bot=bot, trace_id="t2",
        )
    assert outcome == "review"
    bot.send_message.assert_not_awaited()
    remna.assert_not_awaited()


@pytest.mark.asyncio
async def test_obhod_package_underpaid_not_applied():
    payment = _payment(1.0, plan_code="obhod_500", period=1)
    session = _fake_session(payment)
    apply_pkg = AsyncMock(return_value=True)
    with patch("app.services.obhod_service.apply_obhod_package", apply_pkg), \
         patch.object(yk.settings, "ADMINS", [900]):
        outcome = await yk.handle_successful_payment(
            session=session, payment_id=10, telegram_user_id=555, amount=1.0,
            description="x", bot=AsyncMock(), trace_id="t",
        )
    assert outcome == "review"
    apply_pkg.assert_not_awaited()


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


def test_router_reexports_checkout_resolver():
    from app.services import checkout

    assert legacy_payments.resolve_purchase_amount is checkout.resolve_purchase_amount


@pytest.mark.asyncio
async def test_create_payment_computes_amount_without_caller_amount():
    from app.services.payments import yookassa as yk

    created = {}

    async def _create(data, key):
        created["data"] = data
        return {"id": "pay-900000001", "status": "pending",
                "confirmation": {"confirmation_url": "https://pay.example/1"}}

    with patch("app.services.blocklist.get_user_block_reason", AsyncMock(return_value=None)), \
         patch.object(yk.settings, "YOOKASSA_SHOP_ID", "shop"), \
         patch.object(yk.settings, "YOOKASSA_API_KEY", "key"), \
         patch.object(yk.settings, "YOOKASSA_RETURN_URL", "https://example.com/r"), \
         patch.object(yk, "_create_yookassa_payment", side_effect=_create), \
         patch.object(yk, "SessionLocal", None):
        with pytest.raises(ValueError, match="БД не настроена"):
            await yk.create_payment(description="x", user_id=900000001, plan_code="standard", period_months=3)
    assert created["data"]["amount"]["value"] == f"{get_plan_price('standard', 3)}.00"


@pytest.mark.asyncio
async def test_create_payment_refuses_legacy_plan_to_stranger():
    from app.services.payments import yookassa as yk

    with patch("app.services.blocklist.get_user_block_reason", AsyncMock(return_value=None)), \
         patch("app.services.users.get_user_last_plan", AsyncMock(return_value="lite")), \
         patch.object(yk, "_create_yookassa_payment", AsyncMock()) as create:
        with pytest.raises(ValueError, match="недоступен"):
            await yk.create_payment(description="x", user_id=900000001, plan_code="basic", period_months=1)
    create.assert_not_called()
