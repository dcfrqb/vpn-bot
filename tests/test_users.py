# tests/test_users.py
"""Тесты для сервиса работы с пользователями"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.users import (
    get_or_create_telegram_user,
    update_user_activity,
    get_user_active_subscription
)
from app.db.models import TelegramUser, Subscription


@pytest.mark.asyncio
async def test_get_or_create_telegram_user_new():
    """Тест создания нового пользователя"""
    with patch('app.services.users.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем отсутствие пользователя
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result
        
        # Мокируем refresh
        mock_session.refresh = AsyncMock()
        
        user = await get_or_create_telegram_user(
            telegram_id=123456789,
            username="test_user",
            first_name="Test",
            last_name="User"
        )
        
        assert user is not None
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_or_create_telegram_user_existing():
    """Тест обновления существующего пользователя"""
    with patch('app.services.users.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        # Мокируем существующего пользователя
        existing_user = TelegramUser(
            telegram_id=123456789,
            username="old_username",
            first_name="Old",
            last_name="Name"
        )
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_user
        mock_session.execute.return_value = mock_result
        
        mock_session.refresh = AsyncMock()
        
        user = await get_or_create_telegram_user(
            telegram_id=123456789,
            username="new_username",
            first_name="New",
            last_name="Name"
        )
        
        assert user is not None
        assert user.username == "new_username"
        mock_session.add.assert_not_called()
        mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_get_user_active_subscription():
    """Тест получения активной подписки"""
    with patch('app.services.users.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session
        
        subscription = Subscription(
            id=1,
            telegram_user_id=123456789,
            active=True,
            valid_until=datetime.utcnow() + timedelta(days=30),
            plan_code="premium"
        )
        sub_result = MagicMock()
        sub_result.scalar_one_or_none.return_value = subscription
        mock_session.execute.return_value = sub_result
        
        result = await get_user_active_subscription(123456789, use_cache=False)
        
        assert result is not None
        assert result.id == 1
        assert result.active is True


@pytest.mark.asyncio
async def test_get_user_active_subscription_no_user():
    """Тест получения подписки для несуществующего пользователя"""
    with patch('app.services.users.SessionLocal') as mock_session_local:
        mock_session = AsyncMock()
        mock_session_local.return_value.__aenter__.return_value = mock_session

        user_result = MagicMock()
        user_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = user_result

        result = await get_user_active_subscription(999999999)

        assert result is None


# =============================================================================
# is_legacy_user — cohort-функция для разводки тарифов basic/premium ↔ lite/standard/pro.
# Мокаем Redis и SessionLocal, реальных подключений нет.
# =============================================================================


def _make_session_returning_count(count: int):
    """Хелпер: SessionLocal-мок, у которого session.execute().scalar_one() == count."""
    scalar_one_mock = MagicMock(return_value=count)
    execute_result = MagicMock()
    execute_result.scalar_one = scalar_one_mock

    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)

    SessionLocal = MagicMock(return_value=session)
    return SessionLocal


class TestIsLegacyUserCacheLayer:
    """Поведение Redis-кэша до выхода в БД."""

    @pytest.mark.asyncio
    async def test_cache_hit_b1_returns_true_skips_db(self):
        from app.services import users

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=b"1")

        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            with patch("app.db.session.SessionLocal", None):
                result = await users.is_legacy_user(12345)

        assert result is True
        redis_mock.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cache_hit_b0_returns_false_skips_db(self):
        from app.services import users

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=b"0")

        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            with patch("app.db.session.SessionLocal", None):
                result = await users.is_legacy_user(12345)

        assert result is False

    @pytest.mark.asyncio
    async def test_no_redis_falls_through_to_db(self):
        from app.services import users

        SessionLocal_mock = _make_session_returning_count(0)
        with patch("app.services.cache.get_redis_client", return_value=None):
            with patch("app.db.session.SessionLocal", SessionLocal_mock):
                result = await users.is_legacy_user(99)

        assert result is False
        SessionLocal_mock.assert_called_once()


class TestIsLegacyUserDbBranch:
    """Поведение DB-запроса (cache miss)."""

    @pytest.mark.asyncio
    async def test_legacy_when_count_gt_zero(self):
        from app.services import users

        SessionLocal_mock = _make_session_returning_count(1)
        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.setex = AsyncMock()

        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            with patch("app.db.session.SessionLocal", SessionLocal_mock):
                result = await users.is_legacy_user(42)

        assert result is True
        redis_mock.setex.assert_awaited_once()
        args = redis_mock.setex.await_args.args
        assert args[0] == "legacy:42"
        assert args[1] == 300
        assert args[2] == b"1"

    @pytest.mark.asyncio
    async def test_not_legacy_when_count_zero(self):
        from app.services import users

        SessionLocal_mock = _make_session_returning_count(0)
        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)
        redis_mock.setex = AsyncMock()

        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            with patch("app.db.session.SessionLocal", SessionLocal_mock):
                result = await users.is_legacy_user(42)

        assert result is False
        args = redis_mock.setex.await_args.args
        assert args[2] == b"0"

    @pytest.mark.asyncio
    async def test_db_exception_fail_open_to_new_cohort(self):
        """При падении БД — fallback в False (new-cohort), приоритет ARPU."""
        from app.services import users

        boom_session = AsyncMock()
        boom_session.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        boom_session.__aenter__ = AsyncMock(return_value=boom_session)
        boom_session.__aexit__ = AsyncMock(return_value=None)
        SessionLocal_mock = MagicMock(return_value=boom_session)

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)

        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            with patch("app.db.session.SessionLocal", SessionLocal_mock):
                result = await users.is_legacy_user(42)

        assert result is False

    @pytest.mark.asyncio
    async def test_session_local_none_returns_false(self):
        from app.services import users

        with patch("app.services.cache.get_redis_client", return_value=None):
            with patch("app.db.session.SessionLocal", None):
                result = await users.is_legacy_user(42)
        assert result is False


class TestInvalidateLegacyCohortCache:
    @pytest.mark.asyncio
    async def test_delete_called(self):
        from app.services import users

        redis_mock = AsyncMock()
        redis_mock.delete = AsyncMock()
        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            await users.invalidate_legacy_cohort_cache(777)
        redis_mock.delete.assert_awaited_once_with("legacy:777")

    @pytest.mark.asyncio
    async def test_no_redis_is_noop(self):
        from app.services import users

        with patch("app.services.cache.get_redis_client", return_value=None):
            await users.invalidate_legacy_cohort_cache(777)


class TestGetUserLastPlan:
    """get_user_last_plan: для кнопки 'Продлить' в UI."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_plan(self):
        from app.services import users

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=b"standard")

        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            with patch("app.db.session.SessionLocal", None):
                result = await users.get_user_last_plan(42)
        assert result == "standard"

    @pytest.mark.asyncio
    async def test_cache_hit_sentinel_returns_none(self):
        from app.services import users

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=b"-")
        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            with patch("app.db.session.SessionLocal", None):
                result = await users.get_user_last_plan(42)
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_garbage_treated_as_none(self):
        from app.services import users

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=b"trial")  # trial не purchasable
        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            with patch("app.db.session.SessionLocal", None):
                result = await users.get_user_last_plan(42)
        assert result is None

    @pytest.mark.asyncio
    async def test_db_error_returns_none(self):
        from app.services import users

        boom = AsyncMock()
        boom.execute = AsyncMock(side_effect=RuntimeError("BOOM"))
        boom.__aenter__ = AsyncMock(return_value=boom)
        boom.__aexit__ = AsyncMock(return_value=None)
        SessionLocal_mock = MagicMock(return_value=boom)

        redis_mock = AsyncMock()
        redis_mock.get = AsyncMock(return_value=None)

        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            with patch("app.db.session.SessionLocal", SessionLocal_mock):
                result = await users.get_user_last_plan(42)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_redis_no_db_returns_none(self):
        from app.services import users

        with patch("app.services.cache.get_redis_client", return_value=None):
            with patch("app.db.session.SessionLocal", None):
                result = await users.get_user_last_plan(42)
        assert result is None


class TestInvalidateLastPlanCache:
    @pytest.mark.asyncio
    async def test_delete_called(self):
        from app.services import users

        redis_mock = AsyncMock()
        redis_mock.delete = AsyncMock()
        with patch("app.services.cache.get_redis_client", return_value=redis_mock):
            await users.invalidate_last_plan_cache(123)
        redis_mock.delete.assert_awaited_once_with("last_plan:123")

    @pytest.mark.asyncio
    async def test_no_redis_is_noop(self):
        from app.services import users

        with patch("app.services.cache.get_redis_client", return_value=None):
            await users.invalidate_last_plan_cache(123)


class TestLegacyCutoffConstant:
    def test_cutoff_is_aware_datetime(self):
        from app.core.plans import LEGACY_CUTOFF
        from datetime import timezone, timedelta as _td
        assert LEGACY_CUTOFF.tzinfo is not None
        assert LEGACY_CUTOFF.tzinfo.utcoffset(LEGACY_CUTOFF) == _td(0)

    def test_cutoff_in_past_or_imminent(self):
        from app.core.plans import LEGACY_CUTOFF
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        # Sanity: cutoff не должен оказаться в далёком будущем по ошибке.
        assert LEGACY_CUTOFF < _dt.now(_tz.utc) + _td(days=365)


