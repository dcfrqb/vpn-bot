"""3.0 Foundation: infra/redis primitives and shims at the old paths."""
from unittest.mock import patch

import pytest

from app.infra.redis import cache, flags, locks
from tests.fakes.redis import FakeRedis


@pytest.fixture
def redis():
    r = FakeRedis()
    with patch("app.services.cache.get_redis_client", return_value=r):
        yield r


def test_old_paths_are_the_same_modules():
    import app.services.redis_flags as old_flags
    import app.services.user_lock as old_lock

    assert old_flags is flags and old_lock is locks
    from app.services.user_lock import user_action_lock  # noqa: F401
    from app.services.redis_flags import set_once  # noqa: F401


async def test_incr_counter(redis):
    assert await flags.incr_counter("c") == 1
    assert await flags.incr_counter("c", ttl=60) == 2
    assert await flags.incr_counter("t", ttl=60) == 1 and redis.ttl["t"] == 60


async def test_incr_counter_redis_down():
    with patch("app.services.cache.get_redis_client", return_value=None):
        assert await flags.incr_counter("c") is None
    r = FakeRedis()
    r.down = True
    with patch("app.services.cache.get_redis_client", return_value=r):
        assert await flags.incr_counter("c") is None


async def test_marker_lifecycle(redis):
    assert await flags.acquire_marker("k", "t1", 900) == flags.MARKER_ACQUIRED
    assert await flags.acquire_marker("k", "t2", 900) == flags.MARKER_IN_PROGRESS
    await flags.mark_marker_done("k", "t1", 86400)
    assert await flags.acquire_marker("k", "t3", 900) == flags.MARKER_DUPLICATE
    await flags.release_marker("k")
    assert await flags.acquire_marker("k", "t4", 900) == flags.MARKER_ACQUIRED


async def test_marker_unavailable():
    with patch("app.services.cache.get_redis_client", return_value=None):
        assert await flags.acquire_marker("k", "t", 900) == flags.MARKER_UNAVAILABLE


async def test_leader_lock_acquire_renew_contend_lose(redis):
    a = locks.LeaderLock("L", ttl=90)
    b = locks.LeaderLock("L", ttl=90)
    assert await a.ensure() is True
    assert await b.ensure() is False
    redis.ttl["L"] = 5
    assert await a.ensure() is True and redis.ttl["L"] == 90  # renewed
    redis.expire_now("L")
    assert await b.ensure() is True  # b takes over
    assert await a.ensure() is False and a.held is False  # a notices the loss
    await b.release()
    assert "L" not in redis.store


async def test_leader_lock_redis_unavailable():
    with patch("app.services.cache.get_redis_client", return_value=None):
        assert await locks.LeaderLock("L").ensure() is None


async def test_user_action_lock_still_serializes(redis):
    async with locks.user_action_lock("promo", 1) as first:
        async with locks.user_action_lock("promo", 1) as second:
            assert first is True and second is False
    assert not redis.store


async def test_json_cache(redis):
    assert await cache.get_json("s") is None
    assert await cache.set_json("s", {"a": 1, "t": "ы"}, ttl=30) is True
    assert await cache.get_json("s") == {"a": 1, "t": "ы"} and redis.ttl["s"] == 30
    redis.store["bad"] = b"\x80not json"
    assert await cache.get_json("bad") is None
    await cache.invalidate("s")
    assert await cache.get_json("s") is None


async def test_json_cache_redis_down():
    r = FakeRedis()
    r.down = True
    with patch("app.services.cache.get_redis_client", return_value=r):
        assert await cache.get_json("s") is None
        assert await cache.set_json("s", 1, ttl=1) is False
        await cache.invalidate("s")
