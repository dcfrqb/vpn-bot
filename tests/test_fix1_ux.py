"""Фикс-раунд 1, UX-минорки: legacy-экраны тарифов, «Проверить оплату» для
возвращенного/отмененного платежа, поддержка на экране ручной проверки."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

USER_ID = 900000081


def _cb(data):
    cb = MagicMock(spec=types.CallbackQuery)
    cb.from_user = types.User(id=USER_ID, is_bot=False, first_name="Test")
    cb.data = data
    cb.answer = AsyncMock()
    cb.bot = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    cb.message.reply_markup = None
    return cb


@pytest.mark.asyncio
@pytest.mark.parametrize("data,handler", [("plan_basic_3", "plan_basic_period"),
                                          ("plan_premium_12", "plan_premium_period")])
async def test_stale_legacy_plan_screen_refuses_without_pay_button(data, handler):
    from app.routers import start as start_router

    cb = _cb(data)
    sm = MagicMock()
    sm.navigate = AsyncMock()
    with patch("app.services.users.get_user_last_plan", AsyncMock(return_value="lite")), \
         patch("app.ui.screen_manager.get_screen_manager", return_value=sm):
        await getattr(start_router, handler)(cb)
    sm.navigate.assert_not_awaited()
    text = cb.message.edit_text.await_args.args[0]
    assert "недоступен" in text
    kb = cb.message.edit_text.await_args.kwargs["reply_markup"]
    assert not any((b.callback_data or "").startswith("pay_yookassa_") for row in kb.inline_keyboard for b in row)


@pytest.mark.asyncio
async def test_legacy_plan_screen_for_owner_shows_catalog_price():
    from app.core.plans import get_plan_price
    from app.routers import start as start_router
    from app.ui.screens.subscription import SubscriptionPlanDetailScreen

    cb = _cb("plan_basic_3")
    sm = MagicMock()
    sm.navigate = AsyncMock()
    create_vm = AsyncMock(return_value=object())
    with patch("app.services.users.get_user_last_plan", AsyncMock(return_value="basic")), \
         patch("app.ui.screen_manager.get_screen_manager", return_value=sm), \
         patch.object(SubscriptionPlanDetailScreen, "create_viewmodel", create_vm):
        await start_router.plan_basic_period(cb)
    assert create_vm.await_args.kwargs["amount"] == get_plan_price("basic", 3)
    sm.navigate.assert_awaited_once()


async def _check_payment(status):
    from app.legacy.routers import payments as lp

    cb = _cb("check_payment:ext-900")
    payment = SimpleNamespace(external_id="ext-900")

    class _R:
        def scalar_one_or_none(self):
            return payment

    session = MagicMock()
    session.execute = AsyncMock(return_value=_R())
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch.object(lp, "SessionLocal", MagicMock(return_value=cm)), \
         patch.object(lp, "check_payment_rate_limit", AsyncMock(return_value=(True, 0))), \
         patch.object(lp, "recheck_single_payment", AsyncMock(return_value={"status": status})):
        await lp.handle_check_payment(cb)
    return cb.message.edit_text.await_args


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expect", [
    ("refunded", "Платеж возвращен"),
    ("canceled", "Платеж отменен"),
    ("review_rejected", "не подтвержден"),
    ("weird_status", "пока не определен"),
])
async def test_check_payment_final_statuses_are_human(status, expect):
    call = await _check_payment(status)
    text = call.args[0]
    assert expect in text
    assert status not in text


@pytest.mark.asyncio
async def test_check_payment_review_screen_has_support_button():
    call = await _check_payment("review")
    kb = call.kwargs["reply_markup"]
    urls = [b.url for row in kb.inline_keyboard for b in row if b.url]
    assert urls and urls[0].startswith("https://t.me/")


def test_support_handle_from_settings():
    from app import keyboards

    with patch.object(keyboards, "support_handle", wraps=keyboards.support_handle):
        with patch("app.config.settings.ADMIN_SUPPORT_USERNAME", "@help_test"):
            assert keyboards.support_handle() == "help_test"
