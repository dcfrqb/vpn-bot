"""Хотфикс 2.1, п.9 (Д-2): повтор вебхука после 503 не глотается дедупом."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.payments import webhook as yk  # 3.0: the webhook body moved here
from app.services.payments.errors import ProvisioningPendingError, WebhookRetryableError
from tests.test_hotfix_paid_no_squad import FakeRedis


WEBHOOK = {"event": "payment.succeeded", "object": {"id": "pay-1", "status": "succeeded"}}


@pytest.mark.asyncio
async def test_retry_after_failure_is_processed_and_then_deduped():
    redis = FakeRedis()
    body = AsyncMock(side_effect=[ProvisioningPendingError("remna down"), True])
    with patch("app.services.cache.get_redis_client", return_value=redis), \
         patch.object(yk, "_process_payment_webhook_body", body):
        with pytest.raises(ProvisioningPendingError):
            await yk.process_payment_webhook(dict(WEBHOOK), bot=AsyncMock())
        assert redis.store == {}, "маркер снят после неуспеха"

        assert await yk.process_payment_webhook(dict(WEBHOOK), bot=AsyncMock()) is True
        assert body.await_count == 2, "повтор YooKassa реально обработан"
        assert "yk_event:payment.succeeded:pay-1" in redis.store

        # третья доставка уже обработанного события — дубль
        assert await yk.process_payment_webhook(dict(WEBHOOK), bot=AsyncMock()) is True
        assert body.await_count == 2


@pytest.mark.asyncio
async def test_false_result_releases_marker():
    redis = FakeRedis()
    body = AsyncMock(return_value=False)
    with patch("app.services.cache.get_redis_client", return_value=redis), \
         patch.object(yk, "_process_payment_webhook_body", body):
        assert await yk.process_payment_webhook(dict(WEBHOOK), bot=AsyncMock()) is False
    assert redis.store == {}


@pytest.mark.asyncio
async def test_different_events_of_same_object_not_deduped_together():
    redis = FakeRedis()
    body = AsyncMock(return_value=True)
    with patch("app.services.cache.get_redis_client", return_value=redis), \
         patch.object(yk, "_process_payment_webhook_body", body):
        await yk.process_payment_webhook({"event": "payment.succeeded", "object": {"id": "x"}}, bot=None)
        await yk.process_payment_webhook({"event": "payment.canceled", "object": {"id": "x"}}, bot=None)
    assert body.await_count == 2


@pytest.mark.asyncio
async def test_yookassa_api_unavailable_is_retryable():
    """3.0: the provider view comes from the container's PaymentGateway."""
    from app.container import build_container, set_container
    from app.services.money import money
    from tests.fakes.bot import make_bot
    from tests.fakes.payments import FakePaymentGateway
    from tests.money.fakes import FakeHooks, InMemoryPaymentStore

    class DownGateway(FakePaymentGateway):
        async def get_payment(self, external_id):
            return None

    bot, _ = make_bot()
    c = build_container(bot, payments=DownGateway())
    money(c, store=InMemoryPaymentStore(), hooks=FakeHooks())
    set_container(c)
    redis = FakeRedis()
    webhook = {"event": "payment.succeeded", "object": {
        "id": "pay-2", "status": "succeeded", "paid": True,
        "amount": {"value": "129.00", "currency": "RUB"},
        "created_at": "2026-09-23T00:00:00.000Z", "test": False,
        "recipient": {"account_id": "1", "gateway_id": "1"}, "refundable": True, "metadata": {},
    }}
    try:
        with patch("app.services.cache.get_redis_client", return_value=redis):
            with pytest.raises(WebhookRetryableError):
                await yk.process_payment_webhook(webhook, bot=AsyncMock())
    finally:
        set_container(None)
    assert redis.store == {}
