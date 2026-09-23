"""Stream C flows: maintenance mode through the real dispatcher (admin toggle,
closed panel screens, open checkout)."""
from app.domain.texts.notify import MAINTENANCE_CHECKOUT_NOTICE, MAINTENANCE_SCREEN



async def test_admin_toggle_through_the_dispatcher(flow, monkeypatch):
    admin = flow.user.model_copy(update={"id": 900000199})
    monkeypatch.setattr("app.config.settings.ADMINS", [900000199])
    await flow.send("/maintenance", u=admin)
    msgs = flow.session.calls_of("SendMessage")
    assert msgs and "выключен" in msgs[-1].text
    assert msgs[-1].keyboard[0][0]["data"] == "ad:maint:on:"

    await flow.press("ad:maint:on:", u=admin)
    assert await flow.container.maintenance.is_active()
    assert await flow.container.maintenance.reason() == "вручную"

    flow.maintenance_mw.reset_cache()
    flow.session.reset()
    await flow.press("dv:list:")  # ordinary user: devices are closed
    assert flow.answers()[0].params["text"] == MAINTENANCE_SCREEN

    await flow.press("ad:maint:off:", u=admin)
    assert not await flow.container.maintenance.is_active()


async def test_non_admin_cannot_toggle(flow, monkeypatch):
    monkeypatch.setattr("app.config.settings.ADMINS", [900000199])
    await flow.press("ad:maint:on:")
    assert not await flow.container.maintenance.is_active()


async def test_help_and_checkout_stay_open_during_maintenance(flow, monkeypatch):
    await flow.container.maintenance.set_active(True, reason="test", by=1)
    flow.maintenance_mw.reset_cache()
    calls = []

    async def fake_handle_action(self, **kw):
        calls.append(kw["action"])
        return True

    monkeypatch.setattr("app.ui.screen_manager.ScreenManager.handle_action", fake_handle_action)
    await flow.press("help")
    assert calls == ["open"]
    assert not [a for a in flow.answers() if a.params.get("text") == MAINTENANCE_SCREEN]

    await flow.press("buy_subscription")
    notices = [c for c in flow.session.calls_of("SendMessage") if c.text == MAINTENANCE_CHECKOUT_NOTICE]
    assert len(notices) == 1
