"""Фикс-раунд 1, ревью M1 (money) / M1 (UX): после полного возврата юзер не
остается навсегда без доступа. Возврат -> повторная оплата / /trial / выдача
админом снова дают рабочий VPN. Плюс m2 (обход при возврате продления Pro) и
тексты возврата для юзера и админа.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.payments import refunds as rf
from tests.fakes.remnawave import FakeRemna
from tests.test_hotfix_refunds import _call, _setup

TG_ID = 555  # из _setup в test_hotfix_refunds


async def _refund_first_purchase():
    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=25)
    bot = AsyncMock()
    assert await _call(fake, session, refund, api_payment, bot) is True
    # панель по своему расписанию переводит истекшего юзера в EXPIRED
    fake.users[9]["status"] = "EXPIRED"
    return fake, bot


class _Res:
    def __init__(self, obj=None):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


async def _repay(fake, months=1, plan="lite", approved=False):
    """Путь оплаты: выдача в Remnawave + верификация, как в handle_successful_payment."""
    from app.services.payments import yookassa as yk

    target = datetime.utcnow() + timedelta(days=30 * months)
    tg = SimpleNamespace(telegram_id=TG_ID, remna_user_id="9", username=None, first_name=None, last_name=None)
    sub = SimpleNamespace(id=7, plan_code=plan, remnawave_expected_expire_at=target,
                          valid_until=None, config_data={}, remna_user_id="9")
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_Res(tg), _Res(sub)])
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch.object(yk, "SessionLocal", MagicMock(return_value=cm)), \
         patch.object(yk, "RemnaClient", return_value=fake):
        url = await yk.get_or_create_remna_user_and_get_subscription_url(
            telegram_user_id=TG_ID, subscription_id=7, period_months=months,
            enable_if_disabled=approved,
        )
        ok, _actual, err = await yk._verify_remnawave_synced("9", target, "t", plan_code=plan)
    return url, ok, err


# test_refund_then_repay_gives_working_access: removed in 3.0 with the 2.x provisioning (covered by tests/panel and tests/money)


# test_disabled_user_payment_refused_until_admin_approves: removed in 3.0 with the 2.x provisioning (covered by tests/panel and tests/money)


@pytest.mark.asyncio
@pytest.mark.parametrize("tariff", ["trial_standard_5d", "premium_1"])
async def test_refund_then_trial_or_admin_grant_gives_access(tariff):
    """/trial (trial_standard_5d) и выдача админом (premium_1) идут через provision_tariff.
    После возврата юзер EXPIRED, выдача его оживляет."""
    from app.services import remna_service

    fake, _ = await _refund_first_purchase()
    assert fake.users[9]["status"] == "EXPIRED"
    with patch.object(remna_service, "RemnaClient", return_value=fake), \
         patch.object(remna_service, "ensure_user_in_remnawave", AsyncMock(return_value="9")), \
         patch("app.db.session.SessionLocal", None):
        ok = await remna_service.provision_tariff(TG_ID, tariff, req_id="t")
    assert ok is True
    assert fake.users[9]["status"] == "ACTIVE"
    assert fake.enabled == []
    exp = datetime.fromisoformat(fake.users[9]["expireAt"].replace("Z", "+00:00"))
    assert exp > datetime.now(timezone.utc) + timedelta(days=4)


@pytest.mark.asyncio
async def test_sun718_revert_does_not_reenable():
    from app.services.remna_tariff import apply_tariff_to_remna_user

    fake = FakeRemna()
    fake.add_user(9, "tg_test_user", telegram_id=TG_ID, squads=["pro"], status="DISABLED")
    await apply_tariff_to_remna_user(fake, "9", "lite", expire_at=None, set_device_limit=False)
    assert fake.users[9]["status"] == "DISABLED"
    assert fake.enabled == []


@pytest.mark.asyncio
async def test_refund_texts_for_admin_and_user():
    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=25)
    bot = AsyncMock()
    await _call(fake, session, refund, api_payment, bot)
    by_chat = {c.kwargs["chat_id"]: c.kwargs["text"] for c in bot.send_message.await_args_list}
    admin = by_chat[900]
    assert "129 из 129 ₽" in admin
    assert "None" not in admin and "129.0" not in admin
    assert "мес." in admin
    assert "Возврат оформлен" in by_chat[TG_ID]


@pytest.mark.asyncio
async def test_refund_texts_without_plan_have_no_none():
    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=25)
    payment.payment_metadata = {}
    payment.subscription_id = None
    bot = AsyncMock()
    await _call(fake, session, refund, api_payment, bot)
    admin = [c.kwargs["text"] for c in bot.send_message.await_args_list if c.kwargs["chat_id"] == 900][0]
    assert "None" not in admin
    assert "Тариф: —" in admin


@pytest.mark.asyncio
async def test_refund_of_pro_renewal_shortens_obhod_too():
    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=70, plan="pro", amount=499.0, refunded=499.0)
    fake.add_user(19, "tg_555_obhod", squads=["obhod"],
                  expire=fake.users[9]["expireAt"])
    obhod = SimpleNamespace(active=True, remna_user_id="19", valid_until=None, sub_kind="obhod")
    bot = AsyncMock()
    with patch("app.services.obhod_service.get_obhod_subscription", AsyncMock(return_value=obhod)):
        assert await _call(fake, session, refund, api_payment, bot) is True
    assert fake.users[19]["expireAt"] == fake.users[9]["expireAt"]
    assert obhod.valid_until == sub.valid_until
    admin = [c.kwargs["text"] for c in bot.send_message.await_args_list if c.kwargs["chat_id"] == 900][0]
    assert "обхода укорочен" in admin
    assert rf.REFUND_EXPIRE_NOW  # константа на месте
