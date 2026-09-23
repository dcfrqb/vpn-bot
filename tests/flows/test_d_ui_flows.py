"""Flows for stream D (User UI): /start -> menu, connect, devices, help,
refresh, trial CTA, and old-string aliases. Real dispatcher, fixture ``flow``.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.bot.callbacks import Dev, Nav
from app.domain.models import DeviceInfo, PromoOutcome, SubscriptionState

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def _last_text(flow):
    edited = flow.session.calls_of("EditMessageText")
    if edited:
        return edited[-1].text
    sent = flow.session.calls_of("SendMessage")
    return sent[-1].text if sent else None


async def test_start_shows_main_menu(flow):
    await flow.send("/start")
    text = _last_text(flow)
    assert text is not None
    assert "Профиль" in text
    assert "Подписка не оформлена" in text


async def test_back_to_main_alias_reaches_new_menu_handler(flow):
    await flow.press("back_to_main")
    assert "Профиль" in _last_text(flow)
    assert flow.redis.store["legacy_hits:back_to_main"] == 1


async def test_refresh_forces_status_and_answers_obnovleno(flow):
    await flow.press(Nav(s="main", p="refresh").pack())
    assert flow.status.calls[-1] == (flow.user.id, True)
    texts = [a.params.get("text") for a in flow.answers()]
    assert "Обновлено" in texts


async def test_connect_without_subscription_shows_friendly_screen_not_access_denied(flow):
    await flow.press(Nav(s="connect").pack())
    text = _last_text(flow)
    assert "Доступ запрещен" not in text
    assert "Подписка не активна" in text


async def test_connect_no_subscription_offers_trial_when_available(flow):
    flow.promo.available = True
    await flow.press(Nav(s="connect").pack())
    kb = flow.session.calls_of("EditMessageText")[-1].keyboard
    labels = [b["text"] for row in kb for b in row]
    assert "🎁 Попробовать 5 дней бесплатно" in labels


async def test_connect_with_active_subscription_shows_link(flow):
    flow.status.states[flow.user.id] = SubscriptionState(
        telegram_id=flow.user.id, active=True, plan_code="standard",
        subscription_url="https://sub.example/tok",
    )
    await flow.press(Nav(s="connect").pack())
    text = _last_text(flow)
    assert "<code>https://sub.example/tok</code>" in text


async def test_get_subscription_link_alias_reaches_connect(flow):
    flow.status.states[flow.user.id] = SubscriptionState(
        telegram_id=flow.user.id, active=True, plan_code="lite",
        subscription_url="https://sub.example/tok",
    )
    await flow.press("get_subscription_link")
    assert "<code>https://sub.example/tok</code>" in _last_text(flow)
    assert flow.redis.store["legacy_hits:get_subscription_link"] == 1


async def test_trial_cta_grants_and_opens_connect(flow):
    flow.promo.available = True
    await flow.press(Nav(s="connect", p="trial").pack())

    assert ("start_trial", flow.user.id) in flow.promo.calls
    assert flow.user.id in flow.status.invalidated
    ans = [a.params.get("text") for a in flow.answers()]
    assert any("Пробный период включен" in (t or "") for t in ans)


async def test_trial_cta_shows_alert_when_already_used(flow):
    flow.promo.available = False
    flow.promo.trial_outcome = PromoOutcome.ALREADY_USED
    await flow.press(Nav(s="connect", p="trial").pack())
    ans = flow.answers()
    assert ans and ans[-1].params.get("show_alert") is True
    assert "уже был использован" in ans[-1].params["text"]


async def test_help_screen_and_alias(flow, monkeypatch):
    monkeypatch.setattr("app.config.settings.SUPPORT_HANDLE", None)
    monkeypatch.setattr("app.config.settings.PRIVACY_URL", "https://example.com/privacy")
    await flow.press("help")
    text = _last_text(flow)
    assert "Справка по CRS VPN" in text
    kb = flow.session.calls_of("EditMessageText")[-1].keyboard
    urls = [b["url"] for row in kb for b in row if b.get("url")]
    assert "https://t.me/dcfrq" in urls
    assert "https://example.com/privacy" in urls


async def test_devices_list_and_back_to_main(flow):
    flow.devices_service.devices[flow.user.id] = [
        DeviceInfo(hwid="a" * 16, platform="iOS", device_model="iPhone", updated_at=NOW),
    ]
    await flow.press(Dev(a="list").pack())
    text = _last_text(flow)
    assert "Твои устройства" in text and "iPhone" in text

    await flow.press(Dev(a="back").pack())
    assert "Профиль" in _last_text(flow)


async def test_devices_unlink_flow(flow, monkeypatch):
    monkeypatch.setattr("app.config.settings.DEVICES_UNLINK_ENABLED", True)
    dev = DeviceInfo(hwid="c" * 16, platform="Android", device_model="Pixel", updated_at=NOW)
    flow.devices_service.devices[flow.user.id] = [dev]

    await flow.press(Dev(a="ask", id=dev.short_id).pack())
    assert "Отвязать" in _last_text(flow)

    await flow.press(Dev(a="unlink", id=dev.short_id).pack())
    assert flow.devices_service.unlinked == [(flow.user.id, dev.short_id)]
    text = _last_text(flow)
    assert "Пока ни одно устройство не подключалось" in text


async def test_devices_unlink_disabled_by_flag_keeps_list_hides_button(flow, monkeypatch):
    monkeypatch.setattr("app.config.settings.DEVICES_UNLINK_ENABLED", False)
    flow.devices_service.devices[flow.user.id] = [
        DeviceInfo(hwid="d" * 16, platform="iOS", device_model="iPad", updated_at=NOW),
    ]
    await flow.press(Dev(a="list").pack())
    text = _last_text(flow)
    assert "iPad" in text
    kb = flow.session.calls_of("EditMessageText")[-1].keyboard
    labels = [b["text"] for row in kb for b in row]
    assert not any("Отвязать" in label for label in labels)


async def test_promo_command_requires_code(flow):
    await flow.send("/promo")
    sent = flow.session.calls_of("SendMessage")
    assert "/promo КОД" in sent[-1].text
