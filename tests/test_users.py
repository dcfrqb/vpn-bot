# tests/test_users.py
"""Тесты для сервиса работы с пользователями"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.users import (
    get_or_create_telegram_user,
    get_user_active_subscription
)


@pytest.fixture(autouse=True)
def _no_remna_link_write():
    """Фикс B1: get_or_create_telegram_user после upsert пишет связь с панелью
    отдельным запросом (persist_remna_link, свои тесты в test_fixround3_b1_b2.py).
    Здесь проверяется только upsert, поэтому запись связи заглушена."""
    with patch("app.services.remna_service.persist_remna_link", new=AsyncMock(return_value=True)):
        yield


# Хотфикс 2.1 (чистка тестов по 08 §3): прежние 4 теста патчили
# app.services.users.SessionLocal и проверяли ORM-версию функций (session.add,
# чтение подписки из БД). Сейчас get_or_create_telegram_user делает upsert
# (ON CONFLICT) через app.db.session.SessionLocal, а get_user_active_subscription
# читает Remnawave. Тесты переписаны под текущее поведение.


def _session_factory():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm), session


@pytest.mark.asyncio
async def test_get_or_create_telegram_user_upserts_and_returns_remna_id():
    factory, session = _session_factory()
    with patch("app.services.users.ensure_user_in_remnawave", AsyncMock(return_value="42")), \
         patch("app.db.session.SessionLocal", factory):
        user = await get_or_create_telegram_user(
            telegram_id=123456789, username="test_user", first_name="Test", last_name="User",
        )
    assert user.telegram_id == 123456789
    assert user.remna_user_id == "42"
    session.execute.assert_awaited_once()
    sql = str(session.execute.await_args.args[0])
    assert "ON CONFLICT" in sql.upper()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_user_active_subscription_from_remnawave():
    from app.remnawave.client import RemnaSubscription, RemnaUser

    client = MagicMock()
    client.get_user_with_subscription_by_telegram_id = AsyncMock(return_value=(
        RemnaUser(uuid="7", telegram_id=1, username="u", name="u", raw_data={}),
        RemnaSubscription(active=True, expires_at=datetime.utcnow() + timedelta(days=30), plan=None, raw_data={}),
    ))
    client.close = AsyncMock()
    with patch("app.services.users.RemnaClient", return_value=client):
        result = await get_user_active_subscription(1, use_cache=False)
    assert result is not None and result.active is True
    assert result.remna_user_id == "7"


@pytest.mark.asyncio
async def test_get_user_active_subscription_no_user():
    client = MagicMock()
    client.get_user_with_subscription_by_telegram_id = AsyncMock(return_value=None)
    client.close = AsyncMock()
    with patch("app.services.users.RemnaClient", return_value=client):
        assert await get_user_active_subscription(999999999, use_cache=False) is None


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
        from datetime import timedelta as _td
        assert LEGACY_CUTOFF.tzinfo is not None
        assert LEGACY_CUTOFF.tzinfo.utcoffset(LEGACY_CUTOFF) == _td(0)

    def test_cutoff_in_past_or_imminent(self):
        from app.core.plans import LEGACY_CUTOFF
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        # Sanity: cutoff не должен оказаться в далёком будущем по ошибке.
        assert LEGACY_CUTOFF < _dt.now(_tz.utc) + _td(days=365)


