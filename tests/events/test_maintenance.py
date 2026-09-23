"""Stream C: panel health probe, automatic/manual maintenance, middleware, admin toggle."""
from __future__ import annotations

import pytest

from app.bot.middlewares.maintenance import (
    BLOCKED,
    CHECKOUT,
    PASS,
    MaintenanceMiddleware,
    classify_callback,
    classify_message,
)
from app.domain.models import AdminTopic
from app.domain.texts.notify import MAINTENANCE_CHECKOUT_NOTICE, MAINTENANCE_SCREEN
from app.services import maintenance as M
from app.services.shims import RedisMaintenanceGuard
from tests.fakes.bot import callback_update, make_bot, message_update, user

# ------------------------------------------------------------------ probe


@pytest.fixture
def mon(env):
    env.settings.MAINTENANCE_AUTO_ENABLED = True
    return M.PanelHealthMonitor(env.c.remna, RedisMaintenanceGuard(), env.notifier, env.settings)


async def test_three_failures_switch_auto_maintenance_on_and_first_success_off(env, mon):
    env.panel.healthy = False
    assert [await mon.probe() for _ in range(2)] == ["fail", "fail"]
    assert not await mon.guard.is_active()
    assert await mon.probe() == "down"
    assert await mon.guard.is_active() and M.is_auto_reason(await mon.guard.reason())
    assert await mon.probe() == "down"
    downs = env.notifier.to_admins(AdminTopic.PANEL)
    assert len(downs) == 1 and "Включен режим техработ" in downs[0].text

    env.panel.healthy = True
    assert await mon.probe() == "recovered"
    assert not await mon.guard.is_active()
    assert await mon.probe() == "ok"
    assert len(env.notifier.to_admins(AdminTopic.PANEL)) == 2


async def test_without_auto_flag_only_admins_are_told(env, mon):
    env.settings.MAINTENANCE_AUTO_ENABLED = False
    env.panel.healthy = False
    for _ in range(3):
        await mon.probe()
    assert not await mon.guard.is_active()
    assert "не включался" in env.notifier.to_admins(AdminTopic.PANEL)[0].text
    env.panel.healthy = True
    assert await mon.probe() == "recovered"


async def test_probe_never_clears_a_manual_flag(env, mon):
    await mon.guard.set_active(True, reason="вручную", by=1)
    assert await mon.probe() == "ok"
    assert await mon.guard.is_active()


async def test_admin_switching_auto_off_suppresses_the_probe(env, mon):
    env.panel.healthy = False
    for _ in range(3):
        await mon.probe()
    assert await mon.guard.is_active()
    await M.suppress_auto()
    await mon.guard.set_active(False)
    await mon.probe()
    assert not await mon.guard.is_active()


async def test_redis_down_counts_in_process(env, mon):
    env.redis.down = True
    env.panel.healthy = False
    assert [await mon.probe() for _ in range(3)] == ["fail", "fail", "down"]
    assert len(env.notifier.to_admins(AdminTopic.PANEL)) == 1


async def test_ping_timeout_is_a_failure(env, mon, monkeypatch):
    import asyncio

    async def slow():
        await asyncio.sleep(1)
        return True

    monkeypatch.setattr(M, "PING_TIMEOUT_S", 0.01)
    mon.remna.ping = slow
    assert await mon.probe() == "fail"


async def test_panel_health_job_reuses_one_monitor(env):
    from app.worker.jobs import panel_health

    class Ctx:
        container = env.c

    env.panel.healthy = False
    for _ in range(3):
        await panel_health.run(Ctx())
    assert env.notifier.to_admins(AdminTopic.PANEL)


# ------------------------------------------------------------------ middleware


@pytest.mark.parametrize("data,kind", [
    ("dv:list:", BLOCKED), ("pr:trial:", BLOCKED), ("n:connect:", BLOCKED), ("n:connect:link", BLOCKED),
    ("connect_vpn", BLOCKED), ("get_subscription_link", BLOCKED), ("ui:connect:open:-", BLOCKED),
    ("pe:pro:3", CHECKOUT), ("pl:pro", CHECKOUT), ("pc:5:", CHECKOUT), ("n:plans:", CHECKOUT),
    ("pay_yookassa_pro_1", CHECKOUT), ("buy_subscription", CHECKOUT), ("check_payment:abc", CHECKOUT),
    ("ui:subscription_plans:obhod:-", CHECKOUT),
    ("help", PASS), ("n:main:", PASS), ("back_to_main", PASS), ("bc:close", PASS), ("", PASS), (None, PASS),
])
def test_classify_callback(data, kind):
    assert classify_callback(data) == kind


@pytest.mark.parametrize("text,kind", [("/trial", BLOCKED), ("/trial@crs_bot", BLOCKED), ("/devices", BLOCKED),
                                       ("/start", PASS), ("/start promo", PASS), ("hi", PASS), (None, PASS)])
def test_classify_message(text, kind):
    assert classify_message(text) == kind


class Guard:
    def __init__(self, active):
        self.active = active

    async def is_active(self):
        return self.active


async def test_middleware_blocks_passes_and_notices_checkout_once():
    bot, s = make_bot()
    mw = MaintenanceMiddleware(Guard(True), admin_ids=lambda: [42])
    seen = []

    async def handler(event, data):
        seen.append(getattr(event, "data", None) or event.text)

    u = user(1)
    await mw(handler, callback_update(bot, u, "dv:list:").callback_query.as_(bot), {})
    await mw(handler, callback_update(bot, u, "help").callback_query.as_(bot), {})
    await mw(handler, callback_update(bot, u, "pe:pro:1").callback_query.as_(bot), {})
    await mw(handler, callback_update(bot, u, "pe:pro:3").callback_query.as_(bot), {})
    await mw(handler, message_update(u, "/trial").message.as_(bot), {})
    await mw(handler, callback_update(bot, user(42), "dv:list:").callback_query.as_(bot), {})
    assert seen == ["help", "pe:pro:1", "pe:pro:3", "dv:list:"]
    alerts = s.calls_of("AnswerCallbackQuery")
    assert alerts[0].params["text"] == MAINTENANCE_SCREEN and alerts[0].params["show_alert"] is True
    notices = [c for c in s.calls_of("SendMessage") if c.text == MAINTENANCE_CHECKOUT_NOTICE]
    assert len(notices) == 1
    assert [c.text for c in s.calls_of("SendMessage") if c.text == MAINTENANCE_SCREEN]


def test_maintenance_alert_fits_telegram_limit():
    assert len(MAINTENANCE_SCREEN) <= 200
