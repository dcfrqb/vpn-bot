"""3.0 Foundation: LegacyAliasMiddleware unit behaviour (dispatcher-level proof is in tests/flows)."""
from unittest.mock import patch

import pytest
from aiogram import F, Router

from app.bot.callbacks import Nav, Period
from app.bot.legacy_aliases import (
    ALIASES,
    LegacyAliasMiddleware,
    alias_hit_counts,
    find_alias,
    new_handler_accepts,
    rewrite,
)
from tests.fakes.bot import callback_update, make_bot, user
from tests.fakes.redis import FakeRedis


@pytest.fixture
def redis():
    r = FakeRedis()
    with patch("app.services.cache.get_redis_client", return_value=r):
        yield r


def _cq(bot, data):
    return callback_update(bot, user(), data).callback_query.as_(bot)


def test_alias_keys_unique():
    keys = [a.key for a in ALIASES]
    assert len(keys) == len(set(keys))


def test_amount_in_old_pay_button_is_ignored():
    assert rewrite("pay_yookassa_pro_1_1") == Period(c="pro", m=1).pack()
    assert rewrite("pay_yookassa_pro_0") is None


def test_unknown_strings_are_not_aliases():
    for s in ("n:main:", "bc:close", "random", "", None):
        assert find_alias(s) is None


async def test_passthrough_when_no_new_handler(redis):
    bot, _ = make_bot()
    mw = LegacyAliasMiddleware(lambda: [Router(name="empty")])
    seen = []

    async def handler(event, data):
        seen.append((event.data, data.get("legacy_alias")))

    await mw(handler, _cq(bot, "back_to_main"), {})
    assert seen == [("back_to_main", None)]
    assert redis.store["legacy_hits:back_to_main"] == 1


async def test_rewrite_when_new_handler_accepts(redis):
    bot, _ = make_bot()
    r = Router(name="r3_test")

    @r.callback_query(Nav.filter(F.s == "plans"))
    async def plans(cq):  # pragma: no cover - only filters are evaluated
        return None

    mw = LegacyAliasMiddleware(lambda: [r])
    seen = []

    async def handler(event, data):
        seen.append((event.data, data.get("legacy_alias"), event.bot is bot))

    await mw(handler, _cq(bot, "buy_subscription"), {})
    await mw(handler, _cq(bot, "back_to_main"), {})  # Nav(s=main): not accepted -> passthrough
    assert seen == [("n:plans:", "buy_subscription", True), ("back_to_main", None, True)]
    assert await alias_hit_counts() == {**{a.key: 0 for a in ALIASES if a.count},
                                        "buy_subscription": 1, "back_to_main": 1}


async def test_router_level_filters_are_respected(redis):
    bot, _ = make_bot()
    r = Router(name="r3_admin_only")
    r.callback_query.filter(F.from_user.id == 1)

    @r.callback_query(Nav.filter())
    async def any_nav(cq):  # pragma: no cover
        return None

    assert await new_handler_accepts([r], _cq(bot, Nav(s="main").pack()), {}) is False


async def test_filter_crash_falls_back_to_passthrough(redis):
    bot, _ = make_bot()
    r = Router(name="r3_broken")

    def broken(cq):
        raise RuntimeError("bug in filter")

    @r.callback_query(broken)
    async def h(cq):  # pragma: no cover
        return None

    mw = LegacyAliasMiddleware(lambda: [r])
    seen = []

    async def handler(event, data):
        seen.append(event.data)

    await mw(handler, _cq(bot, "help"), {})
    assert seen == ["help"]


async def test_counter_fail_open_without_redis():
    bot, _ = make_bot()
    mw = LegacyAliasMiddleware(lambda: [])
    seen = []

    async def handler(event, data):
        seen.append(event.data)

    with patch("app.services.cache.get_redis_client", return_value=None):
        await mw(handler, _cq(bot, "help"), {})
    assert seen == ["help"]
