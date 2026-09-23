"""Foundation flows through the real Dispatcher (3.0 after the cutover)."""
from contextlib import contextmanager

import pytest
from aiogram import F
from aiogram.types import CallbackQuery

from app.bot.callbacks import Nav
from app.bot.routers import NEW_ROUTER_MODULES, ROUTERS, SITE_LOGIN_MODULE
from app.routers import site_login
from tests.fakes.bot import user


@contextmanager
def temp_handler(router, callback, *filters):
    """Register a callback handler on a (session-attached) router for one test."""
    router.callback_query.register(callback, *filters)
    handler = router.callback_query.handlers[-1]
    try:
        yield
    finally:
        router.callback_query.handlers.remove(handler)


def test_router_order_site_login_first_then_new_then_errors(flow):
    names = [r.name for r in flow.dp.sub_routers]
    assert names[0] == "site_login"
    assert names[1:1 + len(NEW_ROUTER_MODULES)] == [
        "r3_promo_deeplink", "r3_trial_promo", "r3_start", "r3_menu", "r3_checkout", "r3_connect",
        "r3_devices", "r3_support", "r3_refund", "r3_admin_payments", "r3_admin_home", "r3_admin_users",
        "r3_admin_grants", "r3_admin_ops", "r3_admin_promo", "r3_admin_broadcast", "r3_admin_obhod",
        "r3_admin_panel", "r3_fallback",
    ]
    assert names[1 + len(NEW_ROUTER_MODULES):] == ["tg_errors_global"]
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




async def test_unknown_callback_is_answered_by_the_fallback_with_the_main_menu(flow):
    from app.domain.texts.common import STALE_BUTTON

    await flow.press("ui:no_such_screen:open:-")
    assert [c.params.get("text") for c in flow.answers()] == [STALE_BUTTON]
    assert flow.session.calls_of("EditMessageText") or flow.session.calls_of("SendMessage")


async def test_maintenance_blocks_connect_for_users_but_not_admins(flow, monkeypatch):
    from app.domain.texts.notify import MAINTENANCE_SCREEN

    admin = 900000199
    monkeypatch.setattr("app.config.settings.ADMINS", [admin])
    await flow.container.maintenance.set_active(True, reason="test", by=1)
    flow.maintenance_mw.reset_cache()
    await flow.press("connect_vpn")
    assert [a.params.get("text") for a in flow.answers()] == [MAINTENANCE_SCREEN]
    before = len(flow.answers())
    await flow.press("connect_vpn", u=user(admin))
    assert all(a.params.get("text") != MAINTENANCE_SCREEN for a in flow.answers()[before:])


async def test_native_broadcast_close_button_still_works(flow):
    await flow.press("bc:close")
    assert flow.session.calls_of("DeleteMessage")
    assert not any(k.startswith("legacy_hits:") for k in flow.redis.store)




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
