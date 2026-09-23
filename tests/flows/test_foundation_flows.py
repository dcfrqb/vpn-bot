"""Foundation flows through the real Dispatcher (behaviour of 2.1.1 kept)."""
from contextlib import contextmanager

import pytest
from aiogram import F
from aiogram.types import CallbackQuery

from app.bot.callbacks import Nav
from app.bot.routers import NEW_ROUTER_MODULES, ROUTERS, SITE_LOGIN_MODULE
from app.routers import site_login


@contextmanager
def temp_handler(router, callback, *filters):
    """Register a callback handler on a (session-attached) router for one test."""
    router.callback_query.register(callback, *filters)
    handler = router.callback_query.handlers[-1]
    try:
        yield
    finally:
        router.callback_query.handlers.remove(handler)


def test_router_order_site_login_first_then_new_then_legacy(flow):
    names = [r.name for r in flow.dp.sub_routers]
    assert names[0] == "site_login"
    assert names[1:1 + len(NEW_ROUTER_MODULES)] == [
        "r3_promo_deeplink", "r3_start", "r3_menu", "r3_checkout", "r3_connect", "r3_devices",
        "r3_support", "r3_refund", "r3_admin_payments", "r3_admin_promo", "r3_admin_broadcast",
        "r3_admin_obhod", "r3_admin_panel",
    ]
    assert names[1 + len(NEW_ROUTER_MODULES):] == [
        "ui", "start", "legacy_payments", "admin_broadcast", "admin", "legacy_callbacks", "tg_errors_global",
    ]
    assert ROUTERS[0] == SITE_LOGIN_MODULE


async def test_site_login_deep_link_is_handled_before_start(flow, monkeypatch):
    monkeypatch.setattr("app.config.settings.SITE_INTERNAL_TOKEN", None)
    await flow.send("/start login_" + "A" * 20)
    sent = flow.session.calls_of("SendMessage")
    assert [c.text for c in sent] == [site_login.FEATURE_OFF_TEXT]


async def test_sitelogin_callback_passthrough_not_counted(flow, monkeypatch):
    monkeypatch.setattr("app.config.settings.SITE_INTERNAL_TOKEN", None)
    await flow.press("sitelogin:ok:abc")
    assert [c.params.get("text") for c in flow.answers()] == [site_login.FEATURE_OFF_TEXT]
    assert not any(k.startswith("legacy_hits:") for k in flow.redis.store)


async def test_alias_rewrites_when_new_handler_exists_and_answers_via_bound_bot(flow):
    # D landed the real "main" screen: the alias rewrite now reaches it
    # directly (goes through the bound Bot -> RecordingSession).
    await flow.press("back_to_main")

    ans = flow.answers()
    assert len(ans) == 1  # render() answers the callback (empty text)
    edited = flow.session.calls_of("EditMessageText") or flow.session.calls_of("SendMessage")
    assert edited and "Профиль" in edited[-1].text
    assert flow.redis.store["legacy_hits:back_to_main"] == 1


async def test_alias_passes_through_to_old_handler_when_no_new_handler(flow, monkeypatch):
    # "admin_panel" (Nav(s="admin_panel")) has no new-router owner yet: the
    # alias middleware must still hand the ORIGINAL 2.x string ("ui:admin_panel:open:-")
    # to the 2.x ui router unchanged.
    calls = []

    async def fake_handle_action(self, **kw):
        calls.append((kw["screen_id"].value, kw["action"]))
        return True

    monkeypatch.setattr("app.ui.screen_manager.ScreenManager.handle_action", fake_handle_action)
    await flow.press("ui:admin_panel:open:-")
    assert calls == [("admin_panel", "open")]
    assert len(flow.answers()) == 1
    assert flow.redis.store["legacy_hits:ui"] == 1


async def test_packed_callback_without_handler_falls_to_legacy_catch_all(flow):
    # "plans" is not landed yet (owner: A): a raw packed Nav for it still
    # falls through to the 2.x catch-all.
    await flow.press(Nav(s="plans").pack())
    texts = [c.params.get("text") for c in flow.answers()]
    assert texts == ["❌ Устаревший формат запроса"]  # 2.x legacy_callbacks behaviour


async def test_native_broadcast_close_button_still_works(flow):
    await flow.press("bc:close")
    assert flow.session.calls_of("DeleteMessage")
    assert not any(k.startswith("legacy_hits:") for k in flow.redis.store)


async def test_maintenance_blocks_users_but_not_admins(flow, monkeypatch):
    # Stream C: only panel-dependent screens are closed (connect, devices, trial);
    # see tests/flows/test_maintenance_flows.py for the open ones.
    await flow.container.maintenance.set_active(True, reason="test", by=1)
    flow.maintenance_mw.reset_cache()
    await flow.press("connect_vpn")
    ans = flow.answers()
    assert len(ans) == 1 and ans[0].params.get("show_alert") is True
    assert "технические работы" in ans[0].params["text"].lower()

    flow.session.reset()
    admin = flow.user.model_copy(update={"id": 900000199})
    monkeypatch.setattr("app.config.settings.ADMINS", [900000199])
    calls = []

    async def fake_handle_action(self, **kw):
        calls.append(kw["action"])
        return True

    monkeypatch.setattr("app.ui.screen_manager.ScreenManager.handle_action", fake_handle_action)
    # "admin_panel" has no new-router owner yet, so this still exercises the
    # 2.x ScreenManager path (unlike "help", now landed by D).
    await flow.press("ui:admin_panel:open:-", u=admin)
    assert calls == ["open"]


async def test_new_router_errors_are_generic_and_reported(flow):
    from app.bot.routers import support
    from app.domain.models import AdminTopic
    from app.domain.texts.common import GENERIC_ERROR_ALERT

    async def boom(callback: CallbackQuery):
        raise RuntimeError("secret internals /opt/x password=1")

    with temp_handler(support.router, boom, Nav.filter(F.s == "qa_boom_test")):
        await flow.press(Nav(s="qa_boom_test").pack())

    ans = flow.answers()
    assert [a.params["text"] for a in ans] == [GENERIC_ERROR_ALERT]
    assert "secret" not in str([c.params for c in flow.session.calls])
    errs = flow.notifier.to_admins(AdminTopic.ERRORS)
    assert len(errs) == 1 and "RuntimeError" in errs[0].text


@pytest.mark.parametrize("legacy", ["plan_premium_3", "pay_yookassa_pro_1_449", "check_payment:2f8a-uuid"])
async def test_money_aliases_pass_through_while_stream_a_not_landed(flow, monkeypatch, legacy):
    from app.bot import legacy_aliases

    async def no_new(*a, **k):
        return False

    # count only; the old handler itself is not exercised here (DB/YooKassa)
    monkeypatch.setattr(legacy_aliases, "new_handler_accepts", no_new)
    seen = []

    async def spy(self, handler, event, data):
        seen.append(event.data)
        return None

    monkeypatch.setattr("app.middlewares.timing.TimingMiddleware.__call__", spy)
    await flow.press(legacy)
    assert seen == [legacy]
