"""Фикс-раунд 1: кнопки «Одобрить и выдать» / «Отклонить» под алертом о
платеже на ручной проверке (ревью m5). Только админ, идемпотентно, выдача
та же, что у обычной успешной оплаты."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from app.services.payments import review as rv
from app.services.payments import yookassa as yk
from app.services.payments.errors import ProvisioningPendingError
from tests.fakes.redis import FakeRedis

ADMIN_ID = 900000090
USER_ID = 900000091


class _Res:
    def __init__(self, obj):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


def _session(payment):
    session = MagicMock()
    session.execute = AsyncMock(return_value=_Res(payment))
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return session, MagicMock(return_value=cm)


def _held(plan_code="pro", period=12, amount=1.0, **meta):
    m = {"plan_code": plan_code, "period_months": period, "needs_review": True,
         "review_reason": "сумма не совпала", "review_alerted": True}
    m.update(meta)
    return SimpleNamespace(
        id=10, external_id="ext-10", provider="yookassa", amount=amount, currency="RUB",
        subscription_id=None, status="succeeded", paid_at=None, payment_metadata=m,
        telegram_user_id=USER_ID, description="CRS VPN",
    )


def _patches(session_factory, handler=None):
    ps = [
        patch("app.db.session.SessionLocal", session_factory),
        patch("app.services.cache.get_redis_client", return_value=FakeRedis()),
    ]
    if handler is not None:
        ps.append(patch("app.services.payments.yookassa.handle_successful_payment", handler))
    return ps


async def _decide(payment, approve, handler=None):
    _s, factory = _session(payment)
    ps = _patches(factory, handler)
    for p in ps:
        p.start()
    try:
        return await rv.decide_held_payment(payment.id, ADMIN_ID, approve, AsyncMock())
    finally:
        for p in ps:
            p.stop()


@pytest.mark.asyncio
async def test_approve_provisions_through_normal_path():
    payment = _held()
    handler = AsyncMock(return_value=None)
    code, _ = await _decide(payment, True, handler)
    assert code == rv.APPROVED
    assert payment.payment_metadata["review_approved"] is True
    assert payment.payment_metadata["review_decided_by"] == ADMIN_ID
    handler.assert_awaited_once()
    kw = handler.await_args.kwargs
    assert kw["payment_id"] == 10 and kw["telegram_user_id"] == USER_ID and kw["amount"] == 1.0


@pytest.mark.asyncio
async def test_second_approve_after_provisioning_is_noop():
    payment = _held(review_approved=True)
    payment.subscription_id = 77
    handler = AsyncMock()
    code, _ = await _decide(payment, True, handler)
    assert code == rv.ALREADY_DONE
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_then_approve_is_refused():
    payment = _held()
    handler = AsyncMock()
    code, _ = await _decide(payment, False, handler)
    assert code == rv.REJECTED
    assert payment.payment_metadata["review_rejected"] is True
    code, _ = await _decide(payment, True, handler)
    assert code == rv.ALREADY_REJECTED
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_with_panel_down_stays_approved_for_recovery():
    payment = _held()
    handler = AsyncMock(side_effect=ProvisioningPendingError("panel down"))
    code, _ = await _decide(payment, True, handler)
    assert code == rv.PENDING
    assert payment.payment_metadata["review_approved"] is True


@pytest.mark.asyncio
async def test_approve_unpaid_or_not_held_refused():
    payment = _held()
    payment.status = "pending"
    assert (await _decide(payment, True, AsyncMock()))[0] == rv.NOT_PAID
    payment = _held()
    payment.payment_metadata.pop("needs_review")
    assert (await _decide(payment, True, AsyncMock()))[0] == rv.NOT_HELD


@pytest.mark.asyncio
async def test_approve_end_to_end_obhod_package_applies_like_normal_payment():
    """Реальный handle_successful_payment: одобренный пакет обхода применяется,
    юзер получает обычное сообщение."""
    payment = _held(plan_code="obhod_500", period=1)
    _s, factory = _session(payment)
    apply_pkg = AsyncMock(return_value=True)
    bot = AsyncMock()
    with patch("app.db.session.SessionLocal", factory), \
         patch("app.services.cache.get_redis_client", return_value=FakeRedis()), \
         patch("app.services.obhod_service.apply_obhod_package", apply_pkg), \
         patch.object(yk.settings, "ADMINS", [ADMIN_ID]):
        code, _ = await rv.decide_held_payment(10, ADMIN_ID, True, bot)
    assert code == rv.APPROVED
    apply_pkg.assert_awaited_once()
    assert payment.payment_metadata["obhod_package_applied"] is True
    texts = [c.kwargs["text"] for c in bot.send_message.await_args_list if c.kwargs["chat_id"] == USER_ID]
    assert any("Пакет обхода подключен" in t for t in texts)


def _cb(user_id, data):
    cb = MagicMock(spec=types.CallbackQuery)
    cb.from_user = types.User(id=user_id, is_bot=False, first_name="Admin")
    cb.data = data
    cb.answer = AsyncMock()
    cb.bot = AsyncMock()
    cb.message = MagicMock()
    cb.message.html_text = "🚨 <b>Платеж на ручной проверке</b>"
    cb.message.edit_text = AsyncMock()
    cb.message.answer = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_router_non_admin_gets_nothing():
    from app.routers import admin as admin_router

    cb = _cb(USER_ID, "rv_ok:10")
    decide = AsyncMock()
    with patch.object(admin_router, "is_admin", return_value=False), \
         patch.object(rv, "decide_held_payment", decide):
        await admin_router.payment_review_decision(cb)
    decide.assert_not_awaited()
    cb.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_admin_approves_and_keyboard_removed():
    from app.routers import admin as admin_router

    cb = _cb(ADMIN_ID, "rv_ok:10")
    decide = AsyncMock(return_value=(rv.APPROVED, rv.RESULT_TEXT[rv.APPROVED]))
    with patch.object(admin_router, "is_admin", return_value=True), \
         patch.object(rv, "decide_held_payment", decide):
        await admin_router.payment_review_decision(cb)
    decide.assert_awaited_once_with(10, ADMIN_ID, True, cb.bot)
    kw = cb.message.edit_text.await_args.kwargs
    assert kw["reply_markup"] is None
    assert "доступ выдан" in cb.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_hold_alert_has_review_buttons_and_user_gets_support():
    payment = _held()
    payment.payment_metadata.pop("review_alerted")
    session = MagicMock()
    session.commit = AsyncMock()
    bot = AsyncMock()
    with patch.object(yk.settings, "ADMINS", [ADMIN_ID]):
        await yk._hold_payment_for_review(session, payment, USER_ID, "сумма", bot, "t")
    calls = {c.kwargs["chat_id"]: c.kwargs for c in bot.send_message.await_args_list}
    buttons = [b.callback_data for row in calls[ADMIN_ID]["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["rv_ok:10", "rv_no:10"]
    urls = [b.url for row in calls[USER_ID]["reply_markup"].inline_keyboard for b in row if b.url]
    assert urls and urls[0].startswith("https://t.me/")
