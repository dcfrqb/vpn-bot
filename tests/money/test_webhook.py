"""POST /webhook/yookassa body: dedup marker + Fulfillment, 503 on retryable outcomes."""
import pytest

from app.domain.plans import get_plan_price
from app.services.payments.errors import WebhookRetryableError
from tests.fakes.bot import make_bot
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.payments import FakePaymentGateway
from tests.fakes.redis import FakeRedis
from tests.money.fakes import FakeHooks, FakeProvisioning, InMemoryPaymentStore

TG = 700006


@pytest.fixture
def env(monkeypatch):
    from app.container import build_container, set_container
    from app.services.money import money
    from app.services.payments.ui import NullUi

    redis = FakeRedis()
    monkeypatch.setattr("app.services.cache.get_redis_client", lambda: redis)
    bot, _ = make_bot()
    store = InMemoryPaymentStore()
    prov = FakeProvisioning(store)
    c = build_container(bot, payments=FakePaymentGateway(), provisioning=prov, notifier=RecordingNotifier())
    set_container(c)
    m = money(c, store=store, ui=NullUi(), hooks=FakeHooks())
    yield m, c, store, prov
    set_container(None)


async def _paid(m, c):
    res = await m.checkout.start_checkout(TG, await m.checkout.quote(TG, "lite", 1))
    c.payments.succeed(res.intent.external_id)
    return {"event": "payment.succeeded", "object": {"id": res.intent.external_id, "status": "succeeded"}}


async def test_webhook_grants_and_duplicate_delivery_is_ignored(env):
    from app.services.payments.webhook import process_payment_webhook

    m, c, store, prov = env
    data = await _paid(m, c)
    assert await process_payment_webhook(data) is True
    assert await process_payment_webhook(data) is True
    assert len(prov.grants) == 1


async def test_webhook_retryable_when_grant_fails_and_retry_is_processed(env):
    from app.services.payments.webhook import process_payment_webhook

    m, c, store, prov = env
    prov.fail_times = 1
    data = await _paid(m, c)
    with pytest.raises(WebhookRetryableError):
        await process_payment_webhook(data)
    assert await process_payment_webhook(data) is True  # marker released, YooKassa retry works
    assert len(prov.grants) == 1


async def test_webhook_body_is_never_trusted(env):
    from app.services.payments.webhook import process_payment_webhook

    m, c, store, prov = env
    res = await m.checkout.start_checkout(TG, await m.checkout.quote(TG, "pro", 12))
    forged = {"event": "payment.succeeded",
              "object": {"id": res.intent.external_id, "status": "succeeded",
                         "amount": {"value": str(get_plan_price("pro", 12))}}}
    assert await process_payment_webhook(forged) is True  # API still says pending
    assert not prov.grants and (await store.get(res.intent.payment_id)).status == "pending"


async def test_webhook_garbage_and_unknown(env):
    from app.services.payments.webhook import process_payment_webhook

    assert await process_payment_webhook({}) is False
    assert await process_payment_webhook({"event": "payment.succeeded", "object": {"id": "nope"}}) is False


async def test_canceled_event_closes_payment(env):
    from app.services.payments.webhook import process_payment_webhook

    m, c, store, prov = env
    res = await m.checkout.start_checkout(TG, await m.checkout.quote(TG, "lite", 1))
    c.payments.cancel(res.intent.external_id)
    await process_payment_webhook({"event": "payment.canceled", "object": {"id": res.intent.external_id}})
    assert (await store.get(res.intent.payment_id)).status == "canceled"


def test_route_maps_retryable_to_503():
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient

    from app.api.main import app

    with patch("app.api.routes.yookassa._is_yookassa_ip", return_value=True), \
         patch("app.api.routes.yookassa._webhook_rate_limit_ok", new_callable=AsyncMock, return_value=True), \
         patch("app.api.routes.yookassa.bot_instance", new=AsyncMock()), \
         patch("app.services.payments.webhook.process_payment_webhook", new_callable=AsyncMock,
               side_effect=WebhookRetryableError("down")):
        client = TestClient(app)
        for event in ("payment.succeeded", "payment.canceled"):
            r = client.post("/webhook/yookassa", json={"event": event, "object": {"id": "p-1"}})
            assert r.status_code == 503, event
