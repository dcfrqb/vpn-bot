"""Фикс-раунд 2 хотфикса 2.1 (review_A_round2, N1-N6).

N1 — после возврата нотификатор не шлет «истекает сегодня, продлите».
N2 — оплата, промо и админ-грант не включают юзера, отключенного в панели вручную.
N3 — кнопка «Одобрить» и recovery берут один и тот же лок платежа.
N4 — дедуп вебхука: пропавший маркер не считается «done».
N5 — выключатели фоновых задач: непонятное значение = выключено (тест в
     test_fix1_config_flags).
N6 — после таймаута PATCH перепроверка выдачи идет с задержкой.
Все id и имена здесь выдуманные.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.remna_tariff import RemnaUserDisabledError, apply_tariff_to_remna_user
from tests.fakes.redis import FakeRedis
from tests.fakes.remnawave import FakeRemna

TG_ID = 900000201
ADMIN_ID = 900000299


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# N1
# --------------------------------------------------------------------------

async def _notify_run(fake, redis):
    from app.tasks import expiry_notifier as en

    bot = AsyncMock()
    with patch("app.services.cache.get_redis_client", return_value=redis), \
         patch("app.remnawave.client.RemnaClient", return_value=fake), \
         patch.object(en, "_SEND_DELAY", 0), \
         patch.object(fake, "get_users", AsyncMock(return_value={
             "response": {"total": len(fake.users), "users": list(fake.users.values())}}), create=True):
        stats = await en.check_expiry_notifications(bot)
    return stats, bot


@pytest.mark.asyncio
async def test_expire_now_refund_suppresses_today_notice():
    from tests.test_hotfix_refunds import _call, _setup

    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=25)
    redis = FakeRedis()
    assert await _call(fake, session, refund, api_payment, AsyncMock(), redis=redis) is True
    assert any(k.startswith("expiry_notice:0d:") for k in redis.store)

    stats, bot = await _notify_run(fake, redis)
    assert stats["sent_0d"] == 0 and stats["sent_3d"] == 0
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_notifier_still_sends_without_refund():
    """Контроль: тот же юзер без возврата получает «истекает сегодня»."""
    fake = FakeRemna()
    fake.add_user(9, "tg_test_user", telegram_id=TG_ID,
                  expire=_iso(datetime.now(timezone.utc) + timedelta(minutes=5)))
    stats, bot = await _notify_run(fake, FakeRedis())
    if datetime.now(timezone.utc).date() == (datetime.now(timezone.utc) + timedelta(minutes=5)).date():
        assert stats["sent_0d"] == 1
        bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_suppress_windows_for_shortened_refund():
    from app.tasks.expiry_notifier import suppress_expiry_notices

    redis = FakeRedis()
    now = datetime.now(timezone.utc)
    with patch("app.services.cache.get_redis_client", return_value=redis):
        await suppress_expiry_notices(TG_ID, now + timedelta(days=2))   # в пределах 3 дней
        await suppress_expiry_notices(TG_ID + 1, now + timedelta(days=20))  # далекий срок
    d2 = (now + timedelta(days=2)).date().isoformat()
    assert f"expiry_notice:3d:{TG_ID}:{d2}" in redis.store
    assert f"expiry_notice:0d:{TG_ID}:{d2}" not in redis.store  # в тот день напомнить уместно
    assert not any(str(TG_ID + 1) in k for k in redis.store)


# --------------------------------------------------------------------------
# N2
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_refuses_disabled_without_writing():
    fake = FakeRemna()
    fake.add_user(9, "tg_test_user", telegram_id=TG_ID, squads=["lite"], status="DISABLED")
    with pytest.raises(RemnaUserDisabledError):
        await apply_tariff_to_remna_user(fake, "9", "pro", expire_at="2027-01-01T00:00:00Z",
                                         refuse_if_disabled=True)
    assert fake.patches == [] and fake.enabled == []
    # одобрено админом: включает
    await apply_tariff_to_remna_user(fake, "9", "pro", expire_at="2027-01-01T00:00:00Z",
                                     refuse_if_disabled=True, enable_if_disabled=True)
    assert fake.enabled == [9]


@pytest.mark.asyncio
async def test_apply_expired_user_is_revived_by_date_only():
    fake = FakeRemna()
    fake.add_user(9, "tg_test_user", telegram_id=TG_ID, squads=["lite"], status="EXPIRED")
    await apply_tariff_to_remna_user(fake, "9", "lite",
                                     expire_at=_iso(datetime.now(timezone.utc) + timedelta(days=30)),
                                     refuse_if_disabled=True)
    assert fake.users[9]["status"] == "ACTIVE"
    assert fake.enabled == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tariff", ["trial_standard_5d", "premium_1"])
async def test_promo_and_admin_grant_refused_for_disabled_one_alert(tariff):
    from app.services import remna_service

    fake = FakeRemna()
    fake.add_user(9, "tg_test_user", telegram_id=TG_ID, squads=["lite"], status="DISABLED",
                  expire=_iso(datetime.now(timezone.utc) + timedelta(days=10)))
    before = dict(fake.users[9])
    notify = AsyncMock()
    with patch.object(remna_service, "RemnaClient", return_value=fake), \
         patch.object(remna_service, "ensure_user_in_remnawave", AsyncMock(return_value="9")), \
         patch("app.services.cache.get_redis_client", return_value=FakeRedis()), \
         patch("app.services.blocklist.notify_admins", notify), \
         patch("app.db.session.SessionLocal", None):
        assert await remna_service.provision_tariff(TG_ID, tariff, req_id="t1") is False
        assert await remna_service.provision_tariff(TG_ID, tariff, req_id="t2") is False
    assert fake.patches == [] and fake.enabled == []
    assert fake.users[9]["status"] == "DISABLED"
    assert fake.users[9]["expireAt"] == before["expireAt"]
    notify.assert_awaited_once()
    assert "отключен вручную" in notify.await_args.args[0]


class _Res:
    def __init__(self, obj=None):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


def _session(*objs):
    queue = list(objs)
    session = MagicMock()

    async def _execute(*_a, **_k):
        return _Res(queue.pop(0) if queue else None)

    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


def _payment(**meta):
    m = {"plan_code": "lite", "period_months": 1}
    m.update(meta)
    from app.core.plans import get_expected_amount
    return SimpleNamespace(
        id=20, external_id="ext-20", provider="yookassa",
        amount=float(get_expected_amount("lite", 1)), currency="RUB",
        subscription_id=None, status="succeeded", paid_at=None, payment_metadata=m,
    )


# test_payment_for_disabled_user_is_held_for_review_with_one_alert: removed in 3.0 with the 2.x provisioning (covered by tests/money/test_fulfillment.py and tests/panel)


# test_approved_payment_passes_enable_flag: removed in 3.0 with the 2.x provisioning (covered by tests/money/test_fulfillment.py and tests/panel)


# test_disabled_detected_during_sync_holds_for_review: removed in 3.0 with the 2.x provisioning (covered by tests/money/test_fulfillment.py and tests/panel)


# --------------------------------------------------------------------------
# N3
# --------------------------------------------------------------------------

# test_approve_uses_recovery_provision_lock: removed in 3.0 with the 2.x provisioning (covered by tests/money/test_fulfillment.py and tests/panel)


# --------------------------------------------------------------------------
# N4
# --------------------------------------------------------------------------

class _VanishingMarkerRedis(FakeRedis):
    """SET NX не проходит (маркер есть), но к GET первая доставка его уже сняла."""

    def __init__(self, vanish_times=1):
        super().__init__()
        self.vanish_times = vanish_times

    async def set(self, key, value, ex=None, nx=False):
        if nx and self.vanish_times > 0:
            self.vanish_times -= 1
            return None
        return await super().set(key, value, ex=ex, nx=nx)


@pytest.mark.asyncio
async def test_dedup_vanished_marker_is_not_swallowed():
    from app.services.payments import webhook_dedup as wd

    webhook = {"event": "payment.succeeded", "object": {"id": "pay-900000202"}}
    handler = AsyncMock(return_value=True)
    with patch("app.services.cache.get_redis_client", return_value=_VanishingMarkerRedis(1)):
        assert await wd.run_webhook_once(webhook, "payment.succeeded", "t", handler) is True
    handler.assert_awaited_once()  # вторая попытка SET NX взяла маркер

    from app.services.payments.errors import WebhookRetryableError
    handler = AsyncMock(return_value=True)
    with patch("app.services.cache.get_redis_client", return_value=_VanishingMarkerRedis(2)):
        with pytest.raises(WebhookRetryableError):  # 503, а не 200
            await wd.run_webhook_once(webhook, "payment.succeeded", "t", handler)
    handler.assert_not_awaited()


# --------------------------------------------------------------------------
# N6
# --------------------------------------------------------------------------

class _LatePatchRemna(FakeRemna):
    """Ответ на PATCH теряется по таймауту, а сам PATCH ложится чуть позже."""

    async def update_user(self, user_id, **kwargs):
        async def _late():
            await asyncio.sleep(0.05)
            await FakeRemna.update_user(self, user_id, **kwargs)
        asyncio.get_running_loop().create_task(_late())
        raise asyncio.TimeoutError()


@pytest.mark.asyncio
async def test_late_patch_after_timeout_counts_as_granted(monkeypatch):
    from app.services import remna_service

    monkeypatch.setattr(remna_service, "GRANT_RECHECK_DELAY_SECONDS", 0.2)
    fake = _LatePatchRemna()
    fake.add_user(71, "tg_test_user", telegram_id=TG_ID, squads=[], expire="2000-01-01T00:00:00Z")
    with patch.object(remna_service, "RemnaClient", return_value=fake), \
         patch.object(remna_service, "ensure_user_in_remnawave", AsyncMock(return_value="71")), \
         patch("app.db.session.SessionLocal", None):
        assert await remna_service.provision_tariff(TG_ID, "trial_standard_5d", req_id="t") is True
    assert fake.squad_names(71) == ["standard"]


def test_timeout_detection_follows_cause_chain():
    from app.services.remna_service import _is_timeout_error
    from app.services.remna_tariff import RemnaTariffError

    try:
        try:
            raise asyncio.TimeoutError()
        except asyncio.TimeoutError as e:
            raise RemnaTariffError("update_user failed") from e
    except RemnaTariffError as wrapped:
        assert _is_timeout_error(wrapped)
    assert not _is_timeout_error(RemnaTariffError("squad_not_found"))
