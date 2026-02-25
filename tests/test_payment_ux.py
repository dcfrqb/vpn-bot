"""Тесты Payment UX: rate limit, check_payment external_id, webhook secret, autorecheck guard, NOT_FOUND"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.cache import check_payment_rate_limit, try_schedule_autorecheck
from app.keyboards import get_payment_keyboard, get_new_payment_keyboard


class TestCheckPaymentRateLimit:
    """Тесты rate limit для кнопки «Проверить оплату»"""

    @pytest.mark.asyncio
    async def test_rate_limit_allowed_when_redis_unavailable(self):
        """Redis недоступен — разрешаем (allowed=True)"""
        with patch('app.services.cache.get_redis_client', return_value=None):
            allowed, seconds_left = await check_payment_rate_limit(12345, "ext-1")
            assert allowed is True
            assert seconds_left == 0

    @pytest.mark.asyncio
    async def test_rate_limit_first_call_allowed(self):
        """Первый вызов — разрешён"""
        mock_redis = AsyncMock()
        mock_redis.ttl = AsyncMock(return_value=-2)  # ключ не существует
        mock_redis.setex = AsyncMock()

        with patch('app.services.cache.get_redis_client', return_value=mock_redis):
            allowed, seconds_left = await check_payment_rate_limit(12345, "ext-1")
            assert allowed is True
            assert seconds_left == 0
            mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_rate_limit_second_call_within_ttl_blocked(self):
        """Второй вызов в течение TTL — заблокирован"""
        mock_redis = AsyncMock()
        mock_redis.ttl = AsyncMock(return_value=7)  # осталось 7 сек

        with patch('app.services.cache.get_redis_client', return_value=mock_redis):
            allowed, seconds_left = await check_payment_rate_limit(12345, "ext-1")
            assert allowed is False
            assert seconds_left == 7
            mock_redis.setex.assert_not_called()


class TestGetPaymentKeyboard:
    """Тесты клавиатуры оплаты с external_id"""

    def test_keyboard_includes_external_id_in_check_button(self):
        """Кнопка «Проверить оплату» содержит external_id в callback_data"""
        kb = get_payment_keyboard("https://pay.example.com/xxx", "ext-payment-123")
        check_btn = None
        for row in kb.inline_keyboard:
            for btn in row:
                if hasattr(btn, "callback_data") and btn.callback_data and "check_payment" in btn.callback_data:
                    check_btn = btn
                    break
        assert check_btn is not None
        assert check_btn.callback_data == "check_payment:ext-payment-123"


class TestWebhookSecret:
    """Тесты защиты webhook X-Webhook-Secret"""

    def test_webhook_rejects_wrong_secret(self):
        """Webhook возвращает 401 при неверном X-Webhook-Secret"""
        from fastapi.testclient import TestClient
        from app.api.main import app

        with patch('app.api.main.settings') as mock_settings:
            mock_settings.YOOKASSA_WEBHOOK_SECRET = "correct_secret_123"

            client = TestClient(app)
            response = client.post(
                "/webhook/yookassa",
                json={"event": "payment.succeeded", "object": {}},
                headers={"X-Webhook-Secret": "wrong_secret"}
            )
            assert response.status_code == 401


class TestAutorecheckSchedulingGuard:
    """Тесты защиты от повторного планирования auto-recheck"""

    @pytest.mark.asyncio
    async def test_try_schedule_autorecheck_first_call_ok(self):
        """Первый вызов — разрешено (ключа нет)"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)  # NX: ключ установлен

        with patch('app.services.cache.get_redis_client', return_value=mock_redis):
            ok = await try_schedule_autorecheck("ext-123")
            assert ok is True
            mock_redis.set.assert_called_once()
            args = mock_redis.set.call_args
            assert args[1].get("nx") is True
            assert args[1].get("ex") == 180

    @pytest.mark.asyncio
    async def test_try_schedule_autorecheck_second_call_blocked(self):
        """Второй вызов (ключ уже есть) — не разрешено"""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=False)  # NX: ключ уже существует

        with patch('app.services.cache.get_redis_client', return_value=mock_redis):
            ok = await try_schedule_autorecheck("ext-123")
            assert ok is False

    @pytest.mark.asyncio
    async def test_try_schedule_autorecheck_redis_unavailable(self):
        """Redis недоступен — разрешаем (fallback)"""
        with patch('app.services.cache.get_redis_client', return_value=None):
            ok = await try_schedule_autorecheck("ext-123")
            assert ok is True


class TestNotNotFoundKeyboard:
    """Тесты клавиатуры NOT_FOUND"""

    def test_get_new_payment_keyboard_has_create_button(self):
        """get_new_payment_keyboard содержит кнопку «Создать новый платёж»"""
        kb = get_new_payment_keyboard()
        create_btn = None
        for row in kb.inline_keyboard:
            for btn in row:
                if hasattr(btn, "text") and "Создать" in btn.text:
                    create_btn = btn
                    break
        assert create_btn is not None
        assert create_btn.callback_data == "buy_subscription"


class TestRateLimitFallback:
    """Тесты fallback при недоступности Redis"""

    @pytest.mark.asyncio
    async def test_rate_limit_fallback_when_redis_down(self):
        """Redis недоступен — пользователь не блокируется, allowed=True"""
        with patch('app.services.cache.get_redis_client', return_value=None):
            allowed, seconds_left = await check_payment_rate_limit(999, "ext-xyz")
            assert allowed is True
            assert seconds_left == 0
