"""Хотфикс 2.1, п.10: возвраты (refund.succeeded) записываются и отзывают доступ."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import Payment as PaymentModel, Subscription
from app.services.payments import refunds as rf
from app.services.payments.errors import WebhookRetryableError
from tests.fakes.remnawave import FakeRemna
from tests.fakes.redis import FakeRedis


class _Res:
    def __init__(self, obj):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


class _Session:
    def __init__(self, objects):
        self.objects = objects
        self.commit = AsyncMock()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        return _Res(self.objects.get(stmt.column_descriptions[0].get("entity")))


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class _RecordingObhod:
    def __init__(self):
        self.granted, self.revoked = [], []

    async def on_main_granted(self, tg, plan, until, trace):
        self.granted.append((tg, plan, until))

    async def on_main_revoked(self, tg, trace):
        self.revoked.append(tg)


def _setup(expire_in_days: float, amount=129.0, refunded=129.0, period=1, plan="lite"):
    """3.0: the webhook rolls back through ProvisioningService (review
    architecture R1), so the panel side is the real service over FakeRemna and
    an in-memory accounts repo; ``fake.repo`` / ``fake.obhod`` expose them."""
    from tests.panel.conftest import InMemoryAccountsRepo

    fake = FakeRemna()
    until = datetime.now(timezone.utc) + timedelta(days=expire_in_days)
    fake.add_user(9, "tg_a", telegram_id=555, squads=["lite"], expire=_iso(until))
    fake.repo = InMemoryAccountsRepo()
    fake.repo.add_row(555, plan=plan, panel_id=9, until=until.replace(microsecond=0))
    fake.obhod = _RecordingObhod()
    payment = SimpleNamespace(
        id=1, external_id="pay-1", telegram_user_id=555, amount=amount, currency="RUB",
        status="succeeded", subscription_id=7, updated_at=None,
        payment_metadata={"plan_code": plan, "period_months": period},
    )
    sub = SimpleNamespace(id=7, sub_kind="main", remna_user_id="9", active=True,
                          valid_until=None, remnawave_expected_expire_at=None,
                          provisioning_state="synced", last_provisioning_error=None)
    session = _Session({PaymentModel: payment, Subscription: sub})
    refund = {"id": "rf-1", "status": "succeeded", "payment_id": "pay-1", "amount": refunded, "currency": "RUB"}
    api_payment = {"id": "pay-1", "status": "succeeded", "amount": amount, "currency": "RUB",
                   "refunded_amount": refunded, "metadata": {}}
    return fake, payment, sub, session, refund, api_payment


def _main_row(fake):
    return next(r for r in fake.repo.subs.values() if r.sub_kind == "main")


def _run(fake, session, refund, api_payment, bot, redis=None):
    from app.services.provisioning import PanelProvisioningService
    from tests.fakes.remnawave import FakeRemnaGateway

    svc = PanelProvisioningService(FakeRemnaGateway(fake), fake.repo, notifier=AsyncMock(), obhod=fake.obhod,
                                   late_patch_delay_s=0)
    return [
        patch.object(rf, "fetch_refund", AsyncMock(return_value=refund)),
        patch("app.services.payments.yookassa.check_payment_status", AsyncMock(return_value=api_payment)),
        patch("app.db.session.SessionLocal", session),
        patch.object(rf, "_provisioning", return_value=svc),
        patch("app.services.cache.get_redis_client", return_value=redis),
        patch.object(rf.settings, "ADMINS", [900]),
    ]


WEBHOOK = {"event": "refund.succeeded", "object": {"id": "rf-1", "payment_id": "pay-1"}}


async def _call(fake, session, refund, api_payment, bot, redis=None):
    ps = _run(fake, session, refund, api_payment, bot, redis)
    for p in ps:
        p.start()
    try:
        return await rf.handle_refund_webhook(dict(WEBHOOK), bot)
    finally:
        for p in ps:
            p.stop()


@pytest.mark.asyncio
async def test_full_refund_of_first_purchase_expires_access_without_disable():
    """Фикс-раунд 1 (M1): не DISABLED, а expireAt = сейчас (+5 мин), чтобы
    следующая оплата/выдача штатно оживила юзера."""
    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=25)
    bot = AsyncMock()
    assert await _call(fake, session, refund, api_payment, bot) is True
    assert fake.disabled == []
    assert fake.users[9]["status"] != "DISABLED"
    new_exp = datetime.fromisoformat(fake.users[9]["expireAt"].replace("Z", "+00:00"))
    assert timedelta(0) < new_exp - datetime.now(timezone.utc) <= timedelta(minutes=6)
    row = _main_row(fake)
    assert row.active is False
    assert row.provisioning_state == "expired"
    assert fake.obhod.revoked == [555]
    assert payment.status == "refunded"
    assert payment.payment_metadata["refunds"]["rf-1"]["state"] == "done"
    assert payment.payment_metadata["refunded_amount"] == 129.0
    assert any(c.kwargs["chat_id"] == 900 for c in bot.send_message.await_args_list)


@pytest.mark.asyncio
async def test_full_refund_of_renewal_rolls_back_one_period():
    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=70)
    before = datetime.fromisoformat(fake.users[9]["expireAt"].replace("Z", "+00:00"))
    assert await _call(fake, session, refund, api_payment, AsyncMock()) is True
    assert fake.disabled == []
    after = datetime.fromisoformat(fake.users[9]["expireAt"].replace("Z", "+00:00"))
    assert 27 <= (before - after).days <= 31
    row = _main_row(fake)
    assert row.active is True
    assert row.valid_until == after
    assert payment.status == "refunded"


@pytest.mark.asyncio
async def test_partial_refund_only_records_and_alerts():
    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=25, refunded=50.0)
    bot = AsyncMock()
    assert await _call(fake, session, refund, api_payment, bot) is True
    assert fake.patches == [] and fake.disabled == []
    assert payment.status == "succeeded"
    assert payment.payment_metadata["refunds"]["rf-1"]["full"] is False
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_refund_is_idempotent_and_retry_does_not_double_subtract():
    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=70)
    redis = FakeRedis()

    # 1-я доставка: панель падает на PATCH -> 503/повтор, цель уже зафиксирована
    orig_update = fake.update_user
    fake.update_user = AsyncMock(side_effect=RuntimeError("panel down"))
    with pytest.raises(WebhookRetryableError):
        await _call(fake, session, refund, api_payment, AsyncMock(), redis)
    rec = _main_row(fake).config_data["rollbacks"]["refund:rf-1"]
    assert rec["state"] == "pending"
    assert redis.store == {}, "маркер и лок сняты, повтор YooKassa пройдет"

    # 2-я доставка: применяется та же абсолютная цель
    fake.update_user = orig_update
    assert await _call(fake, session, refund, api_payment, AsyncMock(), redis) is True
    assert datetime.fromisoformat(fake.users[9]["expireAt"].replace("Z", "+00:00")) == \
        datetime.fromisoformat(rec["target"])

    # 3-я доставка того же события: дубль, ничего не меняется
    patches_before = len(fake.patches)
    assert await _call(fake, session, refund, api_payment, AsyncMock(), redis) is True
    assert len(fake.patches) == patches_before


@pytest.mark.asyncio
async def test_obhod_package_refund_does_not_touch_access():
    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=25, amount=599.0,
                                                              refunded=599.0, plan="obhod_250")
    assert await _call(fake, session, refund, api_payment, AsyncMock()) is True
    assert fake.patches == [] and fake.disabled == []
    assert payment.status == "refunded"


@pytest.mark.asyncio
async def test_api_unavailable_is_retryable():
    with patch.object(rf, "fetch_refund", AsyncMock(return_value=None)):
        with pytest.raises(WebhookRetryableError):
            await rf.process_refund_webhook(dict(WEBHOOK), AsyncMock())


def test_webhook_endpoint_routes_refund_and_503_on_retry():
    from fastapi.testclient import TestClient
    from app.api import main as api_main
    from app.api.routes import yookassa as yk_routes

    client = TestClient(api_main.app)
    headers = {}
    yk_ip = patch.object(yk_routes, "_get_client_ip", return_value="185.71.76.5")
    with yk_ip, patch.object(yk_routes, "bot_instance", object()), \
         patch.object(yk_routes, "_webhook_rate_limit_ok", AsyncMock(return_value=True)), \
         patch("app.services.payments.refunds.handle_refund_webhook", AsyncMock(return_value=True)) as h:
        r = client.post("/webhook/yookassa", json=WEBHOOK, headers=headers)
    assert r.status_code == 200
    h.assert_awaited_once()

    yk_ip = patch.object(yk_routes, "_get_client_ip", return_value="185.71.76.5")
    with yk_ip, patch.object(yk_routes, "bot_instance", object()), \
         patch.object(yk_routes, "_webhook_rate_limit_ok", AsyncMock(return_value=True)), \
         patch("app.services.payments.refunds.handle_refund_webhook",
               AsyncMock(side_effect=WebhookRetryableError("x"))):
        r = client.post("/webhook/yookassa", json=WEBHOOK, headers=headers)
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_full_refund_of_an_unused_gift_switches_the_code_off():
    """Review money m-3."""
    from app.services.promo_types import PromoCodeRow

    fake, payment, sub, session, refund, api_payment = _setup(expire_in_days=25)
    payment.subscription_id = None
    payment.payment_metadata = {"plan_code": "pro", "period_months": 1, "gift_code": "g_abc"}
    row = PromoCodeRow(id=5, code="g_abc", kind="gift", plan_code="pro", days=31, max_uses=1, uses=0)

    class Repo:
        switched = []

        async def get_code(self, code):
            return row if code == "g_abc" else None

        async def set_active(self, code_id, active):
            self.switched.append((code_id, active))
            return True

    bot = AsyncMock()
    with patch("app.services.promo_repo.SqlPromoRepo", Repo):
        assert await _call(fake, session, refund, api_payment, bot) is True
    assert Repo.switched == [(5, False)]
    assert fake.patches == []
    admin = [c.kwargs["text"] for c in bot.send_message.await_args_list if c.kwargs["chat_id"] == 900][0]
    assert "код отключен" in admin
