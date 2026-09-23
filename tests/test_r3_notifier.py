"""3.0 Foundation: TelegramNotifier routing, fallback, dedup, escaping."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.domain.models import AdminTopic
from app.services.notifications import TelegramNotifier
from app.services.ports import Notifier
from tests.fakes.bot import make_bot
from tests.fakes.redis import FakeRedis


def cfg(**kw):
    base = dict(ADMIN_CHAT_ID=None, ADMINS=[11, 22], ADMIN_TOPIC_PAYMENTS=None, ADMIN_TOPIC_REFUNDS=None,
                ADMIN_TOPIC_PANEL=None, ADMIN_TOPIC_ERRORS=None, ADMIN_TOPIC_PROMO=None, ADMIN_TOPIC_BROADCAST=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def redis():
    r = FakeRedis()
    with patch("app.services.cache.get_redis_client", return_value=r):
        yield r


def test_is_a_notifier():
    bot, _ = make_bot()
    assert isinstance(TelegramNotifier(bot, cfg()), Notifier)


async def test_dm_fallback_without_admin_chat(redis):
    bot, s = make_bot()
    n = TelegramNotifier(bot, cfg())
    assert await n.notify_admins(AdminTopic.PAYMENTS, "a <b> & c") == 2
    sent = s.calls_of("SendMessage")
    assert [c.chat_id for c in sent] == [11, 22]
    assert sent[0].text == "a &lt;b&gt; &amp; c" and sent[0].params["parse_mode"] == "HTML"


async def test_topic_routing(redis):
    bot, s = make_bot()
    n = TelegramNotifier(bot, cfg(ADMIN_CHAT_ID=-100123, ADMIN_TOPIC_REFUNDS=7))
    assert await n.notify_admins(AdminTopic.REFUNDS, "<i>ok</i>", html=True) == 1
    (c,) = s.calls_of("SendMessage")
    assert c.chat_id == -100123 and c.params["message_thread_id"] == 7 and c.text == "<i>ok</i>"
    s.reset()
    await n.notify_admins(AdminTopic.GENERAL, "root")
    assert "message_thread_id" not in s.calls_of("SendMessage")[0].params


async def test_admin_chat_failure_falls_back_to_dm(redis):
    bot, s = make_bot()
    n = TelegramNotifier(bot, cfg(ADMIN_CHAT_ID=-100123, ADMIN_TOPIC_ERRORS=3))
    real = s.make_request

    async def flaky(b, method, timeout=None):
        if getattr(method, "chat_id", None) == -100123:
            s.calls.append(type("C", (), {"method": "FAILED"})())
            raise RuntimeError("chat not found")
        return await real(b, method, timeout)

    s.make_request = flaky
    assert await n.notify_admins(AdminTopic.ERRORS, "x") == 2


async def test_dedup(redis):
    bot, s = make_bot()
    n = TelegramNotifier(bot, cfg())
    assert await n.notify_admins(AdminTopic.PANEL, "down", dedup_key="node:1", dedup_ttl=600) == 2
    assert await n.notify_admins(AdminTopic.PANEL, "down", dedup_key="node:1") == 0
    assert redis.ttl["notify:node:1"] == 600
    assert len(s.calls_of("SendMessage")) == 2


async def test_dedup_fail_open_without_redis():
    bot, s = make_bot()
    n = TelegramNotifier(bot, cfg(ADMINS=[1]))
    with patch("app.services.cache.get_redis_client", return_value=None):
        assert await n.notify_admins(AdminTopic.PANEL, "x", dedup_key="k") == 1
        assert await n.notify_admins(AdminTopic.PANEL, "x", dedup_key="k") == 1


async def test_dedup_released_on_failed_send_admins(redis):
    bot, s = make_bot()
    n = TelegramNotifier(bot, cfg(ADMINS=[1]))
    s.fail["SendMessage"] = RuntimeError("forbidden")
    assert await n.notify_admins(AdminTopic.PANEL, "x", dedup_key="k") == 0
    assert "notify:k" not in redis.store
    del s.fail["SendMessage"]
    assert await n.notify_admins(AdminTopic.PANEL, "x", dedup_key="k") == 1
    assert "notify:k" in redis.store


async def test_dedup_kept_on_successful_send_admins(redis):
    bot, s = make_bot()
    n = TelegramNotifier(bot, cfg(ADMINS=[1]))
    assert await n.notify_admins(AdminTopic.PANEL, "x", dedup_key="k") == 1
    assert "notify:k" in redis.store
    assert await n.notify_admins(AdminTopic.PANEL, "x", dedup_key="k") == 0


async def test_dedup_released_on_failed_send_user(redis):
    bot, s = make_bot()
    n = TelegramNotifier(bot, cfg())
    s.fail["SendMessage"] = RuntimeError("forbidden")
    assert await n.notify_user(5, "x", dedup_key="k") is False
    assert "notify:k" not in redis.store
    del s.fail["SendMessage"]
    assert await n.notify_user(5, "x", dedup_key="k") is True
    assert "notify:k" in redis.store


async def test_notify_user_escapes_and_never_raises(redis):
    bot, s = make_bot()
    n = TelegramNotifier(bot, cfg())
    assert await n.notify_user(5, "<script>") is True
    assert s.calls_of("SendMessage")[0].text == "&lt;script&gt;"
    s.fail["SendMessage"] = RuntimeError("forbidden")
    assert await n.notify_user(5, "x") is False
