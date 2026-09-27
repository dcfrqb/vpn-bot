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
        subscription_url="https://sub.example/abc",
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
        "💡 <b>Подписка не оформлена</b>\n<blockquote>Нажми «Подписка», чтобы подключить VPN.</blockquote>\n\n"
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
    assert "Нажми «Подписка», чтобы продлить доступ." in text


# --- Connect -----------------------------------------------------------


def test_connect_no_subscription_with_trial():
    text, kb = connect_view.no_subscription(trial_available=True, support_handle=None)
    assert text == (
        "ℹ️ <b>Подписка не активна</b>\n<blockquote>Для подключения к VPN нужна активная подписка.</blockquote>\n\n"
        "<i>Попробуй бесплатно или выбери тариф ниже.</i>"
    )
    assert _keyboard_texts(kb) == [
        ["🎁 Попробовать 5 дней бесплатно"], ["💳 Подписка"],
        ["✍️ Поддержка"], ["🏠 В меню"],
    ]


def test_connect_no_subscription_without_trial():
    text, kb = connect_view.no_subscription(trial_available=False)
    assert "Попробуй бесплатно" not in text
    assert _keyboard_texts(kb)[0] == ["💳 Подписка"]


def test_connect_success_pro_with_obhod_and_article():
    state = SubscriptionState(
        telegram_id=1, active=True, plan_code="pro", subscription_url="https://sub.example/abc",
        obhod_active=True, obhod_used_bytes=12_884_901_888, obhod_limit_bytes=107_374_182_400,
        obhod_subscription_url="https://sub.example/obhod-abc",
    )
    text, kb = connect_view.success(state, article_url="https://telegra.ph/how-to")

    assert text == (
        "🚀 <b>Подключение VPN</b>\n\n🔗 <b>Твоя ссылка</b>\n<code>https://sub.example/abc</code>\n\n"
        "💡 <b>Как подключить</b>\n<blockquote>1. Открой ссылку\n2. Скачай подходящий VPN клиент\n"
        "3. Импортируй ссылку подписки в клиент</blockquote>\n\n"
        "🛡 <b>Обход блокировок</b>\n"
        "<blockquote>Отдельная ссылка для мобильного интернета, когда оператор пускает только "
        "в белый список сайтов. 100 ГБ в месяц.\nДобавляется так же, как основная. "
        "Включай обход, когда сайт заблокирован по мобильному интернету, "
        "и выключай, когда все работает штатно.\nИспользовано: 12\xa0ГБ из 100\xa0ГБ</blockquote>\n\n"
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
    assert "Готовим твою ссылку обхода" in text
    assert text.count("Обход блокировок") == 1 and "<code></code>" not in text
    # the text says «нажми «Обновить»», so the button is there (review UX M2)
    assert ["🔄 Обновить"] in _keyboard_texts(kb)


def test_connect_error_has_no_dead_back_button():
    text, kb = connect_view.error(support_handle="dcfrq")
    assert "Не удалось получить ссылку" in text
    assert _keyboard_texts(kb) == [
        ["🔄 Обновить"], ["✍️ Поддержка"], ["🏠 В меню"],
    ]
    assert "Подписка не активна" not in text


# --- Devices -------------------------------------------------------------


def test_devices_list_two_devices():
    devs = [
        DeviceInfo(hwid="aaaaaaaaaaaaaaaa11223344", platform="iOS", device_model="iPhone 15", updated_at=NOW),
        DeviceInfo(hwid="bbbbbbbbbbbbbbbb55667788", platform="Android", device_model="Xiaomi Redmi", updated_at=NOW - timedelta(days=14)),
    ]
    text, kb = devices_view.list_screen(devs, device_limit=5, unlink_enabled=True)

    assert text == (
        "📱 <b>Мои устройства (2 из 5)</b>\n<blockquote>"
        "📱 iPhone 15, последний раз онлайн: сегодня\n"
        "📱 Xiaomi Redmi, последний раз онлайн: 14\xa0дней назад</blockquote>\n\n"
        "<i>Лишнее можно отвязать, освободится место под новое устройство.</i>"
    )
    assert _keyboard_texts(kb) == [
        ["❌ Отвязать: iPhone 15"], ["❌ Отвязать: Xiaomi Redmi"], ["🏠 В меню"],
    ]


def test_devices_empty():
    text, kb = devices_view.list_screen([], device_limit=5, unlink_enabled=True)
    assert "Пока ни одно устройство не подключалось" in text
    assert _keyboard_texts(kb) == [["🏠 В меню"]]


def test_devices_unlink_disabled_hides_unlink_buttons_but_keeps_the_list():
    devs = [DeviceInfo(hwid="a" * 16, platform="iOS", updated_at=NOW)]
    text, kb = devices_view.list_screen(devs, device_limit=5, unlink_enabled=False)
    assert "Мои устройства" in text and "1 из 5" in text
    assert "отвязать" not in text and "напиши в поддержку" in text  # review UX M3
    assert _keyboard_texts(kb) == [["✍️ Поддержка"], ["🏠 В меню"]]


# --- Support / help ---------------------------------------------------------


def test_support_screen_with_privacy_link():
    text, kb = support_view.render(support_handle=None, privacy_url="https://example.com/privacy")
    assert text == (
        "ℹ️ <b>Справка по CRS VPN</b>\n\n🔐 <b>Что такое VPN</b>\n"
        "<blockquote>VPN создает защищенное соединение между твоим устройством и интернетом.</blockquote>\n\n"
        "❓ <b>Частые вопросы</b>\n<blockquote>"
        "<b>VPN не подключается:</b> обнови подписку в самом приложении (кнопка обновления или свайп "
        "вниз по списку серверов) и проверь, что добавлена ссылка из «Подключиться».\n\n"
        "<b>Сколько устройств можно подключить:</b> смотри в разделе «Мои устройства», лимит зависит от тарифа.\n\n"
        "<b>Как сменить устройство:</b> напиши в поддержку, освободим место под новое, и подключи его "
        "той же ссылкой."
        "</blockquote>\n\n<i>Не нашел ответ? Напиши в поддержку.</i>"
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
    assert "отвяжи старое в «Мои устройства»" in text


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
