"""Фикс-раунд 1: общий дедуп вебхуков (F2) и 503 для дубля «в процессе» (m1/m-4)."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.payments import webhook_dedup as wd
from app.services.payments.errors import WebhookRetryableError
from tests.fakes.redis import FakeRedis

WEBHOOK = {"event": "payment.succeeded", "object": {"id": "pay-900000001"}}
KEY = "yk_event:payment.succeeded:pay-900000001"


@pytest.mark.asyncio
async def test_duplicate_while_first_in_progress_is_retryable():
    redis = FakeRedis()
    redis.store[KEY] = "processing:first"
    handler = AsyncMock(return_value=True)
    with patch("app.services.cache.get_redis_client", return_value=redis):
        with pytest.raises(WebhookRetryableError):
            await wd.run_webhook_once(WEBHOOK, "payment.succeeded", "t2", handler)
    handler.assert_not_called()
    assert redis.store[KEY] == "processing:first"  # чужой маркер не тронут


@pytest.mark.asyncio
async def test_success_marks_done_and_next_delivery_is_duplicate():
    redis = FakeRedis()
    handler = AsyncMock(return_value=True)
    with patch("app.services.cache.get_redis_client", return_value=redis):
        assert await wd.run_webhook_once(WEBHOOK, "payment.succeeded", "t1", handler) is True
        assert redis.store[KEY].startswith("done:")
        assert await wd.run_webhook_once(WEBHOOK, "payment.succeeded", "t2", handler) is True
    assert handler.await_count == 1


@pytest.mark.asyncio
async def test_failure_releases_marker_for_retry():
    redis = FakeRedis()
    with patch("app.services.cache.get_redis_client", return_value=redis):
        with pytest.raises(RuntimeError):
            await wd.run_webhook_once(WEBHOOK, "payment.succeeded", "t1", AsyncMock(side_effect=RuntimeError("x")))
        assert KEY not in redis.store
        assert await wd.run_webhook_once(WEBHOOK, "payment.succeeded", "t2", AsyncMock(return_value=False)) is False
        assert KEY not in redis.store


@pytest.mark.asyncio
async def test_legacy_marker_value_counts_as_done():
    """Маркер старого формата (просто trace_id) после деплоя = обработано."""
    redis = FakeRedis()
    redis.store[KEY] = "0b1c-trace"
    handler = AsyncMock(return_value=True)
    with patch("app.services.cache.get_redis_client", return_value=redis):
        assert await wd.run_webhook_once(WEBHOOK, "payment.succeeded", "t2", handler) is True
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_no_redis_processes_without_dedup():
    handler = AsyncMock(return_value=True)
    with patch("app.services.cache.get_redis_client", return_value=None):
        assert await wd.run_webhook_once(WEBHOOK, "payment.succeeded", "t1", handler) is True
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_and_refund_modules_share_public_dedup():
    import inspect
    from app.services.payments import refunds, yookassa

    assert "run_webhook_once" in inspect.getsource(yookassa.process_payment_webhook)
    assert "run_webhook_once" in inspect.getsource(refunds.handle_refund_webhook)
    assert not hasattr(yookassa, "_acquire_webhook_dedup")


@pytest.mark.asyncio
async def test_user_lock_uses_shared_primitive():
    from app.infra.redis.locks import user_action_lock

    redis = FakeRedis()
    with patch("app.services.cache.get_redis_client", return_value=redis):
        async with user_action_lock("promo", 900000001) as first:
            async with user_action_lock("promo", 900000001) as second:
                assert first is True and second is False
        assert "lock:promo:900000001" not in redis.store
