"""Тесты дедупликации уведомлений об истечении подписки"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.subscriptions import (
    process_subscription_notifications,
    try_acquire_expiry_notice_lock,
    EXPIRY_NOTICE_RATE_LIMIT_HOURS,
)
from app.db.models import Subscription, TelegramUser


@pytest.mark.asyncio
async def test_try_acquire_lock_returns_true_when_no_notice():
    """Первый вызов (last_expiry_notice_at IS NULL) — lock приобретается"""
    with patch("app.services.subscriptions.SessionLocal") as mock_sl:
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        acquired = await try_acquire_expiry_notice_lock(1)
        assert acquired is True


@pytest.mark.asyncio
async def test_try_acquire_lock_returns_false_when_recent_notice():
    """Второй вызов в течение 24h (UPDATE не затронул строк) — lock не приобретается"""
    with patch("app.services.subscriptions.SessionLocal") as mock_sl:
        mock_result = MagicMock()
        mock_result.rowcount = 0  # UPDATE не затронул строк — уже отправляли
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        acquired = await try_acquire_expiry_notice_lock(1)
        assert acquired is False


@pytest.mark.asyncio
async def test_process_notifications_skips_when_lock_not_acquired():
    """При rate-limit (lock не приобретён) не отправляем уведомление"""
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    now = datetime.utcnow()
    user = TelegramUser(telegram_id=123456789, username="test")
    sub = Subscription(
        id=1,
        telegram_user_id=123456789,
        active=True,
        valid_until=now + timedelta(hours=12),
        plan_code="premium",
        last_expiry_notice_at=now - timedelta(hours=1),
    )

    async def mock_get_subs(days):
        if days == 0:
            return [{"subscription": sub, "user": user, "days_left": 0, "expires_at": sub.valid_until}]
        return []

    with patch("app.services.subscriptions.get_subscriptions_expiring_soon", side_effect=mock_get_subs):
        with patch("app.services.subscriptions.try_acquire_expiry_notice_lock", new_callable=AsyncMock, return_value=False):
            result = await process_subscription_notifications(mock_bot)

    assert result["notified_0d"] == 0
    mock_bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_process_notifications_sends_when_lock_acquired():
    """При успешном lock отправляем уведомление"""
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    now = datetime.utcnow()
    user = TelegramUser(telegram_id=123456789, username="test")
    sub = Subscription(
        id=1,
        telegram_user_id=123456789,
        active=True,
        valid_until=now + timedelta(hours=12),
        plan_code="premium",
        plan_name="Премиум",
    )

    async def mock_get_subs(days):
        if days == 0:
            return [{"subscription": sub, "user": user, "days_left": 0, "expires_at": sub.valid_until}]
        return []

    with patch("app.services.subscriptions.get_subscriptions_expiring_soon", side_effect=mock_get_subs):
        with patch("app.services.subscriptions.try_acquire_expiry_notice_lock", new_callable=AsyncMock, return_value=True):
            with patch("app.services.subscriptions.SessionLocal", MagicMock()):  # чтобы не выйти по early return
                result = await process_subscription_notifications(mock_bot)

    assert result["notified_0d"] == 1
    mock_bot.send_message.assert_called_once()


def test_expiry_rate_limit_is_24_hours():
    """Дедуп использует ТОЛЬКО 24 часа, не 48h и не другие интервалы"""
    assert EXPIRY_NOTICE_RATE_LIMIT_HOURS == 24


@pytest.mark.asyncio
async def test_try_acquire_lock_fails_when_notice_sent_23h_ago():
    """Моделирует: повтор через 23ч → last_expiry_notice_at ещё не прошло 24ч → rowcount=0 → lock не приобретается"""
    with patch("app.services.subscriptions.SessionLocal") as mock_sl:
        mock_result = MagicMock()
        mock_result.rowcount = 0  # 23h < 24h, WHERE не сработал
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        acquired = await try_acquire_expiry_notice_lock(1)
        assert acquired is False


@pytest.mark.asyncio
async def test_try_acquire_lock_succeeds_when_notice_sent_25h_ago():
    """Моделирует: повтор через 25ч → last_expiry_notice_at прошло >24ч → rowcount=1 → lock приобретается"""
    with patch("app.services.subscriptions.SessionLocal") as mock_sl:
        mock_result = MagicMock()
        mock_result.rowcount = 1  # 25h > 24h, WHERE сработал
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_sl.return_value.__aenter__.return_value = mock_session

        acquired = await try_acquire_expiry_notice_lock(1)
        assert acquired is True


@pytest.mark.asyncio
async def test_two_consecutive_calls_only_first_sends():
    """2 вызова подряд: первый lock=True→отправка, второй lock=False→пропуск"""
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    now = datetime.utcnow()
    user = TelegramUser(telegram_id=123456789, username="test")
    sub = Subscription(
        id=1,
        telegram_user_id=123456789,
        active=True,
        valid_until=now + timedelta(hours=12),
        plan_code="premium",
        plan_name="Премиум",
    )

    async def mock_get_subs(days):
        if days == 0:
            return [{"subscription": sub, "user": user, "days_left": 0, "expires_at": sub.valid_until}]
        return []

    lock_calls = []

    async def mock_try_acquire(sub_id):
        # Первый вызов — lock, второй — нет
        if len(lock_calls) == 0:
            lock_calls.append(sub_id)
            return True
        return False

    with patch("app.services.subscriptions.get_subscriptions_expiring_soon", side_effect=mock_get_subs):
        with patch("app.services.subscriptions.try_acquire_expiry_notice_lock", side_effect=mock_try_acquire):
            with patch("app.services.subscriptions.SessionLocal", MagicMock()):
                result1 = await process_subscription_notifications(mock_bot)
                result2 = await process_subscription_notifications(mock_bot)

    assert result1["notified_0d"] == 1
    assert result2["notified_0d"] == 0
    assert mock_bot.send_message.call_count == 1
