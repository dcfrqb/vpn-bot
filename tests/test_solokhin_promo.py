"""Тесты для промокода /solokhin — 1 месяц basic только для новых пользователей"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram import types


@pytest.fixture
def mock_message():
    """Мок сообщения Telegram"""
    user = types.User(
        id=12345,
        is_bot=False,
        first_name="Test",
        last_name="User",
        username="testuser"
    )
    message = MagicMock(spec=types.Message)
    message.from_user = user
    message.text = "/solokhin"
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_solokhin_grants_only_new_user(mock_message):
    """Промокод /solokhin выдаёт подписку только новому пользователю (count=0)"""
    from app.routers.start import cmd_solokhin

    created_subscription = None

    def capture_upsert(*args, **kwargs):
        nonlocal created_subscription
        defaults = kwargs.get("defaults", {})
        created_subscription = defaults
        sub = MagicMock()
        sub.id = 1
        sub.config_data = defaults.get("config_data", {})
        return sub

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    # count=0 (новый пользователь)
    mock_count_result = MagicMock()
    mock_count_result.scalar.return_value = 0

    mock_session.execute = AsyncMock(return_value=mock_count_result)

    with patch("app.db.session.SessionLocal") as mock_sl, \
         patch("app.services.users.get_or_create_telegram_user", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.services.cache.invalidate_user_cache", new_callable=AsyncMock), \
         patch("app.services.cache.invalidate_subscription_cache", new_callable=AsyncMock), \
         patch("app.services.payments.yookassa.get_or_create_remna_user_and_get_subscription_url", new_callable=AsyncMock, return_value=None):

        mock_sl.return_value.__aenter__.return_value = mock_session
        mock_sl.return_value.__aexit__.return_value = None

        mock_repo = MagicMock()
        mock_repo.upsert_subscription = AsyncMock(side_effect=capture_upsert)

        with patch("app.repositories.subscription_repo.SubscriptionRepo", return_value=mock_repo):
            await cmd_solokhin(mock_message)

    assert created_subscription is not None
    assert created_subscription["plan_code"] == "basic"
    assert created_subscription["config_data"]["source"] == "promo_solokhin"

    mock_message.answer.assert_called_once()
    text = mock_message.answer.call_args[0][0]
    assert "1 месяц" in text or "месяц" in text.lower()
    assert "🎉" in text


@pytest.mark.asyncio
async def test_solokhin_rejects_existing_user(mock_message):
    """Промокод /solokhin отклоняет пользователя с существующей подпиской"""
    from app.routers.start import cmd_solokhin

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    # count=1 (есть подписка)
    mock_count_result = MagicMock()
    mock_count_result.scalar.return_value = 1
    mock_session.execute = AsyncMock(return_value=mock_count_result)

    with patch("app.db.session.SessionLocal") as mock_sl:
        mock_sl.return_value.__aenter__.return_value = mock_session
        mock_sl.return_value.__aexit__.return_value = None

        await cmd_solokhin(mock_message)

    mock_message.answer.assert_called_once()
    text = mock_message.answer.call_args[0][0]
    assert "только новым" in text or "новым пользователям" in text
