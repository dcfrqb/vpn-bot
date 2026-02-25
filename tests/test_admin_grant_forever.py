"""Тесты для admin_grant_forever — выдача премиум навсегда"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from aiogram import types

from app.db.models import AccessRequest, TelegramUser, Subscription


@pytest.fixture
def mock_callback_admin():
    """Мок callback для администратора"""
    user = types.User(
        id=999,
        is_bot=False,
        first_name="Admin",
        last_name="User",
        username="adminuser"
    )
    callback = MagicMock(spec=types.CallbackQuery)
    callback.from_user = user
    callback.data = "admin_grant_forever_42"
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.bot = AsyncMock()
    callback.bot.send_message = AsyncMock()
    return callback


@pytest.fixture
def mock_access_request():
    """Мок AccessRequest"""
    return MagicMock(
        id=42,
        telegram_id=123456789,
        username="testuser",
        name="Test User",
        status="pending"
    )


@pytest.mark.asyncio
async def test_admin_grant_forever_creates_subscription(
    mock_callback_admin, mock_access_request
):
    """Выдача навсегда создаёт подписку, если её нет"""
    from app.routers.admin import admin_grant_forever

    created_subscription = None

    async def capture_upsert(*args, **kwargs):
        nonlocal created_subscription
        defaults = kwargs.get("defaults", {})
        created_subscription = MagicMock()
        created_subscription.id = 1
        created_subscription.config_data = defaults.get("config_data", {})
        created_subscription.plan_code = defaults.get("plan_code")
        created_subscription.valid_until = defaults.get("valid_until")
        created_subscription.last_expiry_notice_at = defaults.get("last_expiry_notice_at")
        return created_subscription

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("app.routers.admin.is_admin", return_value=True), \
         patch("app.services.access_request.get_request_by_id", new_callable=AsyncMock, return_value=mock_access_request), \
         patch("app.services.access_request.approve_request", new_callable=AsyncMock, return_value=True), \
         patch("app.db.session.SessionLocal") as mock_sl, \
         patch("app.services.users.get_or_create_telegram_user", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.services.cache.invalidate_user_cache", new_callable=AsyncMock), \
         patch("app.services.cache.invalidate_subscription_cache", new_callable=AsyncMock), \
         patch("app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url", new_callable=AsyncMock, return_value=None):

        mock_sl.return_value.__aenter__.return_value = mock_session
        mock_sl.return_value.__aexit__.return_value = None

        mock_repo = AsyncMock()
        mock_repo.upsert_subscription = AsyncMock(side_effect=capture_upsert)

        with patch("app.repositories.subscription_repo.SubscriptionRepo", return_value=mock_repo):
            await admin_grant_forever(mock_callback_admin)

    assert created_subscription is not None
    assert created_subscription.plan_code == "premium"
    assert created_subscription.config_data.get("source") == "admin_forever"
    assert created_subscription.last_expiry_notice_at is None
    assert created_subscription.valid_until.year == 2099
    assert created_subscription.valid_until.month == 12
    assert created_subscription.valid_until.day == 31
    assert created_subscription.valid_until.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_admin_grant_forever_updates_existing(
    mock_callback_admin, mock_access_request
):
    """Выдача навсегда обновляет существующую подписку"""
    from app.routers.admin import admin_grant_forever

    updated_subscription = MagicMock()
    updated_subscription.id = 1
    updated_subscription.config_data = {"source": "admin_forever"}
    updated_subscription.plan_code = "premium"
    updated_subscription.valid_until = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    updated_subscription.last_expiry_notice_at = None

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    with patch("app.routers.admin.is_admin", return_value=True), \
         patch("app.services.access_request.get_request_by_id", new_callable=AsyncMock, return_value=mock_access_request), \
         patch("app.services.access_request.approve_request", new_callable=AsyncMock, return_value=True), \
         patch("app.db.session.SessionLocal") as mock_sl, \
         patch("app.services.users.get_or_create_telegram_user", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.services.cache.invalidate_user_cache", new_callable=AsyncMock), \
         patch("app.services.cache.invalidate_subscription_cache", new_callable=AsyncMock), \
         patch("app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url", new_callable=AsyncMock, return_value=None):

        mock_sl.return_value.__aenter__.return_value = mock_session
        mock_sl.return_value.__aexit__.return_value = None

        mock_repo = AsyncMock()
        mock_repo.upsert_subscription = AsyncMock(return_value=updated_subscription)

        with patch("app.repositories.subscription_repo.SubscriptionRepo", return_value=mock_repo):
            await admin_grant_forever(mock_callback_admin)

    mock_repo.upsert_subscription.assert_called_once()
    call_defaults = mock_repo.upsert_subscription.call_args[1]["defaults"]
    assert call_defaults["plan_code"] == "premium"
    assert call_defaults["config_data"]["source"] == "admin_forever"
    assert call_defaults["last_expiry_notice_at"] is None
    assert call_defaults["valid_until"].year == 2099
    assert call_defaults.get("is_lifetime") is True


@pytest.mark.asyncio
async def test_admin_grant_forever_idempotent_when_already_processed(
    mock_callback_admin, mock_access_request
):
    """Повторное нажатие при уже обработанном запросе — идемпотентно, без вызова upsert"""
    from app.routers.admin import admin_grant_forever

    mock_access_request.status = "approved"

    mock_repo = AsyncMock()
    mock_repo.upsert_subscription = AsyncMock()

    with patch("app.routers.admin.is_admin", return_value=True), \
         patch("app.services.access_request.get_request_by_id", new_callable=AsyncMock, return_value=mock_access_request), \
         patch("app.repositories.subscription_repo.SubscriptionRepo", return_value=mock_repo):
        await admin_grant_forever(mock_callback_admin)

    mock_repo.upsert_subscription.assert_not_called()
    mock_callback_admin.answer.assert_called_once()
    answer_args = mock_callback_admin.answer.call_args[0]
    assert any("обработано" in str(a).lower() for a in answer_args)
