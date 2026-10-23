"""Golden text snapshots of every D screen (menu, connect, devices, support).

Pure functions of domain DTOs: no bot, no DB, no network. A change here
means the wording or layout of a screen changed on purpose.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.bot.views import connect as connect_view
from app.bot.views import devices as devices_view
from app.bot.views import menu as menu_view
from app.bot.views import support as support_view
from app.domain.models import DeviceInfo, SubscriptionState

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """datetime whose now() is pinned to NOW, so golden texts do not rot with the calendar."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch):
    import app.domain.models as models_mod
    import app.domain.texts.devices as devices_texts
    import app.domain.texts.menu as menu_texts

    monkeypatch.setattr(models_mod, "datetime", _FrozenDatetime)
    monkeypatch.setattr(devices_texts, "datetime", _FrozenDatetime)
    monkeypatch.setattr(menu_texts, "datetime", _FrozenDatetime)


def _keyboard_texts(markup):
    return [[b.text for b in row] for row in markup.inline_keyboard]


# --- Main menu -------------------------------------------------------------


def test_menu_pro_active_with_obhod():
    state = SubscriptionState(
        telegram_id=1, has_panel_user=True, active=True, plan_code="pro",
        expires_at=NOW + timedelta(days=7), device_limit=10, devices_used=3,
        obhod_active=True, obhod_used_bytes=12_884_901_888, obhod_limit_bytes=107_374_182_400,
        subscription_url="https://sub.example/abc", obhod_subscription_url="https://sub.example/obhod",
    )
    text, kb = menu_view.render(555, "Test User", state, is_admin=False, trial_available=False)

    assert text == (
        "👤 <b>Профиль</b>\n<blockquote>ID: 555\nИмя: Test User</blockquote>\n\n"
        "🟢 <b>Подписка активна</b>\n<blockquote>Тариф: Pro\n📅 До: 30.09.2026\n"
        "⏳ Осталось: 7\xa0дней\nУстройства: 3 из 10</blockquote>\n\n"
        "🛡 <b>Обход блокировок</b>\n<blockquote>Использовано: 12\xa0ГБ из 100\xa0ГБ</blockquote>"
    )
    assert _keyboard_texts(kb) == [
        ["🚀 Подключиться"], ["💳 Подписка"], ["📱 Мои устройства"], ["🔄 Обновить", "ℹ️ Помощь"],
    ]


def test_menu_no_subscription_with_trial_cta():
    state = SubscriptionState(telegram_id=1)
    text, kb = menu_view.render(777, "Vasya", state, is_admin=False, trial_available=True)

    assert text == (
        "👤 <b>Профиль</b>\n<blockquote>ID: 777\nИмя: Vasya</blockquote>\n\n"
        "💡 <b>Подписка не оформлена</b>\n<blockquote>Нажмите «Подписка», чтобы подключить VPN.</blockquote>\n\n"
        "<i>Есть бесплатный пробный период на 5 дней, без карты.</i>"
    )
    assert _keyboard_texts(kb)[1] == ["🎁 Попробовать 5 дней бесплатно"]


def test_menu_admin_gets_admin_panel_button():
    state = SubscriptionState(telegram_id=1)
    _, kb = menu_view.render(1, "Admin", state, is_admin=True, trial_available=False)
    assert _keyboard_texts(kb)[-1] == ["👑 Админ-панель"]


def test_menu_expired_subscription():
    state = SubscriptionState(telegram_id=1, active=False, expires_at=NOW - timedelta(days=2))
    text, _ = menu_view.render(1, "X", state)
    assert "🔴 <b>Подписка истекла</b>" in text
    assert "Нажмите «Подписка», чтобы продлить доступ." in text


# --- Connect -----------------------------------------------------------


def test_connect_no_subscription_with_trial():
    text, kb = connect_view.no_subscription(trial_available=True, support_handle=None)
    assert text == (
        "ℹ️ <b>Подписка не активна</b>\n<blockquote>Для подключения к VPN нужна активная подписка.</blockquote>\n\n"
        "<i>Попробуйте бесплатно или выберите тариф ниже.</i>"
    )
    assert _keyboard_texts(kb) == [
        ["🎁 Попробовать 5 дней бесплатно"], ["💳 Подписка"],
        ["✍️ Поддержка"], ["🏠 В меню"],
    ]


def test_connect_no_subscription_without_trial():
    text, kb = connect_view.no_subscription(trial_available=False)
    assert "Попробуйте бесплатно" not in text
    assert _keyboard_texts(kb)[0] == ["💳 Подписка"]


def test_connect_success_pro_with_obhod_and_article():
    state = SubscriptionState(
        telegram_id=1, active=True, plan_code="pro", subscription_url="https://sub.example/abc",
        obhod_active=True, obhod_used_bytes=12_884_901_888, obhod_limit_bytes=107_374_182_400,
        obhod_subscription_url="https://sub.example/obhod-abc",
    )
    text, kb = connect_view.success(state, article_url="https://telegra.ph/how-to")

    assert text == (
        "🚀 <b>Подключение VPN</b>\n\n🔗 <b>Ваша ссылка</b>\n<code>https://sub.example/abc</code>\n\n"
        "💡 <b>Как подключить</b>\n<blockquote>1. Откройте ссылку\n2. Скачайте подходящий VPN-клиент\n"
        "3. Импортируйте ссылку подписки в клиент</blockquote>\n\n"
        "🛡 <b>Обход блокировок</b>\n"
        "<blockquote>Отдельная ссылка для мобильного интернета, когда оператор разрешает доступ только "
        "к сайтам из белого списка. 150 ГБ в месяц.\nДобавляется так же, как основная. "
        "Включайте обход, когда сайт заблокирован по мобильному интернету, "
        "и выключайте, когда все работает штатно.\nИспользовано: 12\xa0ГБ из 100\xa0ГБ</blockquote>\n\n"
        "<code>https://sub.example/obhod-abc</code>"
    )
    assert _keyboard_texts(kb) == [
        ["🔗 Открыть основную ссылку"], ["🛡 Открыть ссылку обхода"],
        ["➕ Докупить обход"], ["📖 Инструкция"], ["🏠 В меню"],
    ]


def test_connect_success_non_pro_shows_obhod_upsell():
    state = SubscriptionState(telegram_id=1, active=True, plan_code="standard", subscription_url="https://sub.example/x")
    text, _ = connect_view.success(state)
    assert "Есть в тарифе Pro. Отдельная ссылка для мобильного интернета" in text
    assert "<code>https://sub.example/x</code>" in text
    # no second, empty obhod block («использовано 0 ГБ» with an empty link)
    assert text.count("Обход блокировок") == 1 and "Использовано" not in text and "<code></code>" not in text


def test_connect_success_pro_obhod_not_ready_yet():
    state = SubscriptionState(telegram_id=1, active=True, plan_code="pro", subscription_url="https://sub.example/x")
    text, kb = connect_view.success(state)
    assert "Не удалось подтвердить готовность обхода" in text
    assert text.count("Обход блокировок") == 1 and "<code></code>" not in text
    # the text says «нажмите «Обновить»», so the button is there (review UX M2)
    assert ["🔄 Обновить"] in _keyboard_texts(kb)


def test_connect_error_has_no_dead_back_button():
    text, kb = connect_view.error(support_handle="dcfrq")
    assert "Не удалось получить ссылку" in text
    assert _keyboard_texts(kb) == [
        ["🔄 Обновить"], ["✍️ Поддержка"], ["🏠 В меню"],
    ]
    assert "Подписка не активна" not in text


# --- Devices -------------------------------------------------------------


def test_devices_list_cards():
    devs = [
        DeviceInfo(hwid="bbbbbbbbbbbbbbbb55667788", platform="Android", os_version="14", device_model="Xiaomi Redmi",
                   user_agent="v2RayTun/5.13.2", created_at=NOW - timedelta(days=60),
                   updated_at=NOW - timedelta(days=14)),
        DeviceInfo(hwid="aaaaaaaaaaaaaaaa11223344", platform="iOS", os_version="18.1", device_model="iPhone 16",
                   user_agent="Happ/3.1.0/ios CFNetwork/1568", created_at=NOW - timedelta(days=10), updated_at=NOW),
        DeviceInfo(hwid="cccccccccccccccceck-vlad", created_at=NOW - timedelta(days=90),
                   updated_at=NOW - timedelta(days=45)),
    ]
    text, kb = devices_view.list_screen(devs, device_limit=5, unlink_enabled=True)

    assert text == (
        "📱 <b>Мои устройства (3 из 5)</b>\n"
        "<blockquote>📱 <b>1. iPhone 16</b>\niOS 18.1 · Happ 3.1\nДобавлено: 13.09.2026\nОнлайн: сегодня</blockquote>\n\n"
        "<blockquote>📱 <b>2. Xiaomi Redmi</b>\nAndroid 14 · v2RayTun 5.13\nДобавлено: 25.07.2026\n"
        "Онлайн: 14\xa0дней назад</blockquote>\n\n"
        "<blockquote>📶 <b>3. Устройство 3</b>\nДобавлено: 25.06.2026\nОнлайн: 45\xa0дней назад\n"
        "⚠️ Давно не выходило на связь</blockquote>\n\n"
        "<i>Нажмите ❌ с номером устройства, чтобы отвязать его. Место освободится сразу, а если устройство "
        "снова подключится, приложение добавит его заново.</i>"
    )
    assert _keyboard_texts(kb) == [["❌ 1", "❌ 2", "❌ 3"], ["🏠 В меню"]]
    # numbers of the buttons follow the cards (last seen first), not the panel order
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [
        "dv:ask:11223344", "dv:ask:55667788", "dv:ask:eck-vlad"]


def test_devices_one_name_in_card_button_and_confirmation():
    """Review bug: the card said «Неизвестное устройство» while its button said «eck-vlad»."""
    nameless = DeviceInfo(hwid="xxxxxxxxeck-vlad", updated_at=NOW)
    by_os = DeviceInfo(hwid="yyyyyyyy12345678", platform="windows", os_version="11", updated_at=NOW)
    by_app = DeviceInfo(hwid="zzzzzzzz87654321", user_agent="Hiddify/2.5.7 okhttp", updated_at=NOW)
    text, _ = devices_view.list_screen([nameless, by_os, by_app], device_limit=3, unlink_enabled=True)
    assert "<b>1. Устройство 1</b>" in text and "<b>2. Windows 11</b>" in text and "<b>3. Hiddify 2.5</b>" in text
    assert "eck-vlad" not in text
    confirm, kb = devices_view.ask_unlink(nameless, 1)
    assert confirm.startswith("❓ <b>Отвязать Устройство 1?</b>")
    assert _keyboard_texts(kb) == [["✅ Да, отвязать", "✖️ Отмена"]]
    assert devices_view.ask_unlink(by_os, 2)[0].startswith("❓ <b>Отвязать Windows 11?</b>")


def test_devices_client_app_from_user_agent():
    from app.domain.texts.devices import app_title

    assert app_title("Happ/3.1.0/ios CFNetwork/1568 Darwin/24") == "Happ 3.1"
    assert app_title("v2RayTun/5.13.2 (iPhone)") == "v2RayTun 5.13"
    assert app_title("clash-verge/v2.0.3") == "Clash Verge 2.0"
    assert app_title("Shadowrocket/2070 CFNetwork") == "Shadowrocket"
    assert app_title("okhttp/4.12") == app_title("Mozilla/5.0 (X11)") == app_title(None) == ""


def test_devices_empty():
    text, kb = devices_view.list_screen([], device_limit=5, unlink_enabled=True)
    assert "Пока ни одно устройство не подключалось" in text
    assert _keyboard_texts(kb) == [["🏠 В меню"]]


def test_devices_unlink_disabled_hides_unlink_buttons_but_keeps_the_list():
    devs = [DeviceInfo(hwid="a" * 16, platform="iOS", updated_at=NOW)]
    text, kb = devices_view.list_screen(devs, device_limit=5, unlink_enabled=False)
    assert "Мои устройства" in text and "1 из 5" in text
    assert "отвязать" not in text and "напишите в поддержку" in text  # review UX M3
    assert _keyboard_texts(kb) == [["✍️ Поддержка"], ["🏠 В меню"]]


# --- Support / help ---------------------------------------------------------


def test_support_screen_with_privacy_link():
    text, kb = support_view.render(support_handle=None, privacy_url="https://example.com/privacy")
    assert text == (
        "ℹ️ <b>Справка по CRS VPN</b>\n\n🔐 <b>Что такое VPN</b>\n"
        "<blockquote>VPN создает защищенное соединение между вашим устройством и интернетом.</blockquote>\n\n"
        "❓ <b>Частые вопросы</b>\n<blockquote>"
        "<b>VPN не подключается:</b> обновите подписку в самом приложении (кнопка обновления или свайп "
        "вниз по списку серверов) и проверьте, что добавлена ссылка из «Подключиться».\n\n"
        "<b>Сколько устройств можно подключить:</b> смотрите в разделе «Мои устройства», лимит зависит от тарифа.\n\n"
        "<b>Как сменить устройство:</b> напишите в поддержку. Мы освободим место под новое устройство. Затем подключите его "
        "той же ссылкой."
        "</blockquote>\n\n<i>Не нашли ответ? Напишите в поддержку.</i>"
    )
    assert _keyboard_texts(kb) == [
        ["✍️ Поддержка"], ["📄 Оферта"], ["🔒 Политика конфиденциальности"], ["🏠 В меню"],
    ]
    assert kb.inline_keyboard[2][0].url == "https://example.com/privacy"


def test_support_screen_without_privacy_setting_uses_the_2_1_1_document():
    _, kb = support_view.render(support_handle="dcfrq", privacy_url=None)
    assert _keyboard_texts(kb) == [
        ["✍️ Поддержка"], ["📄 Оферта"], ["🔒 Политика конфиденциальности"], ["🏠 В меню"],
    ]
    assert kb.inline_keyboard[2][0].url.startswith("https://telegra.ph/Politika-konfidencialnosti")


def test_help_promises_unlinking_only_with_the_flag():
    text, _ = support_view.render(unlink_enabled=True)
    assert "отвяжите старое в «Мои устройства»" in text


# --- review UX: grace, stale, trial, unknown device count ----------------------


def test_menu_grace_state_is_not_expired():
    state = SubscriptionState(telegram_id=1, active=False, plan_code="lite", expires_at=NOW - timedelta(days=1),
                              grace_until=NOW + timedelta(days=2), subscription_url="https://sub.example/g")
    text, _ = menu_view.render(1, "X", state)
    assert "🟡 <b>Льготный период</b>" in text and "25.09.2026 03:00" in text
    assert "истекла" not in text


def test_connect_success_in_grace_shows_the_link_and_the_note():
    state = SubscriptionState(telegram_id=1, active=False, plan_code="lite", grace_until=NOW + timedelta(days=2),
                              subscription_url="https://sub.example/g")
    text, _ = connect_view.success(state)
    assert "<code>https://sub.example/g</code>" in text and "Льготный период" in text


def test_menu_trial_label_unknown_devices_and_stale_note():
    state = SubscriptionState(telegram_id=1, active=True, plan_code="standard", is_trial=True,
                              expires_at=NOW + timedelta(hours=5), device_limit=5, stale=True)
    text, _ = menu_view.render(1, "X", state)
    assert "Тариф: Standard (пробный)" in text
    assert "Устройства: до 5" in text and "0 из 5" not in text
    assert "\n\n<i>⚠️ Не удалось обновить данные с панели" in text
    assert "⏳ Истекает сегодня" in text  # the last day (5 hours left), not «Осталось: 1 день»


def test_menu_last_day_by_moscow_calendar_says_expires_today():
    state = SubscriptionState(telegram_id=1, active=True, plan_code="lite",
                              expires_at=NOW + timedelta(hours=20), device_limit=2, devices_used=1)
    text, _ = menu_view.render(1, "X", state)
    assert "⏳ Истекает сегодня" in text and "Осталось" not in text
    tomorrow = SubscriptionState(telegram_id=1, active=True, plan_code="lite",
                                 expires_at=NOW + timedelta(hours=22), device_limit=2, devices_used=1)
    text, _ = menu_view.render(1, "X", tomorrow)  # 01:00 MSK next day
    assert "⏳ Осталось: 1\xa0день" in text


@pytest.mark.parametrize("provisioning,phrase", [
    ("pending", "Подождите минуту"),
    ("failed", "Не удалось подготовить обход"),
    ("unknown", "Не удалось подтвердить готовность обхода"),
])
def test_pro_bypass_unavailable_has_truthful_state_and_recovery_actions(provisioning, phrase):
    state = SubscriptionState(1, active=True, plan_code="Pro", subscription_url="https://sub.example/main",
                              obhod_provisioning_state=provisioning)
    text, markup = connect_view.success(state, support_handle="support_test")
    assert phrase in text
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert any(b.callback_data == "n:connect:refresh" for b in buttons)
    assert any(b.url == "https://t.me/support_test" for b in buttons)
    assert not any(b.callback_data == "n:plans:obhod" for b in buttons)
    menu_text, menu_markup = menu_view.render(1, "X", state, support_handle="support_test")
    assert phrase in menu_text
    assert any(b.url == "https://t.me/support_test" for row in menu_markup.inline_keyboard for b in row)


def test_stale_bypass_link_is_not_offered_for_package_purchase():
    state = SubscriptionState(1, active=True, plan_code="pro", subscription_url="https://sub.example/main",
                              obhod_active=True, obhod_subscription_url="https://sub.example/obhod", stale=True)
    text, markup = connect_view.success(state)
    assert "Не удалось подтвердить готовность обхода" in text
    assert not any(b.callback_data == "n:plans:obhod" for row in markup.inline_keyboard for b in row)


def test_disabled_with_future_expiry_is_not_expired_or_offered_trial_or_purchase():
    from app.domain.models import PanelUser
    from app.services.status import build_state

    state = build_state(1, user=PanelUser(501, status="DISABLED", expire_at=NOW + timedelta(days=30)),
                        main_row=None, now=NOW)
    text, markup = menu_view.render(1, "X", state, trial_available=True)
    assert "Подписка отключена" in text and "поддержку" in text
    assert "Истекла" not in text and "продлить" not in text and "пробный" not in text
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "n:connect:trial" not in callbacks and "n:plans:" not in callbacks
    assert any(b.url for row in markup.inline_keyboard for b in row if "Поддержка" in b.text)


@pytest.mark.parametrize("stale,expires", [(True, NOW - timedelta(days=1)), (False, NOW + timedelta(days=1))])
def test_unverified_inactive_subscription_does_not_claim_expiry(stale, expires):
    state = SubscriptionState(1, has_panel_user=True, expires_at=expires, stale=stale)
    text, _ = menu_view.render(1, "X", state)
    assert "Подписка истекла" not in text and "Истекла:" not in text
    assert "Статус подписки не подтвержден" in text
