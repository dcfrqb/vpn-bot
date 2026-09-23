"""Golden text snapshots of every D screen (menu, connect, devices, support).

Pure functions of domain DTOs: no bot, no DB, no network. A change here
means the wording or layout of a screen changed on purpose.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.bot.views import connect as connect_view
from app.bot.views import devices as devices_view
from app.bot.views import menu as menu_view
from app.bot.views import support as support_view
from app.domain.models import DeviceInfo, SubscriptionState

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


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
        "<b>🟢 Подписка активна</b>\n<blockquote>\nТариф: Pro\nДо: 30.09.2026\n"
        "Осталось: 7\xa0дней\nУстройства: 3 из 10\n</blockquote>\n"
        "🛡 Обход: 12\xa0ГБ из 100\xa0ГБ"
    )
    assert _keyboard_texts(kb) == [
        ["🚀 Подключиться"], ["💳 Подписка"], ["📱 Мои устройства"], ["🔄 Обновить", "ℹ️ Помощь"],
    ]


def test_menu_no_subscription_with_trial_cta():
    state = SubscriptionState(telegram_id=1)
    text, kb = menu_view.render(777, "Vasya", state, is_admin=False, trial_available=True)

    assert text == (
        "👤 <b>Профиль</b>\n<blockquote>ID: 777\nИмя: Vasya</blockquote>\n\n"
        "<b>💡 Подписка не оформлена</b>\n<blockquote>Нажми «Подписка» для активации VPN.</blockquote>\n\n"
        "Есть бесплатный пробный период на 5 дней, без карты."
    )
    assert _keyboard_texts(kb)[1] == ["🎁 Попробовать 5 дней бесплатно"]


def test_menu_admin_gets_admin_panel_button():
    state = SubscriptionState(telegram_id=1)
    _, kb = menu_view.render(1, "Admin", state, is_admin=True, trial_available=False)
    assert _keyboard_texts(kb)[-1] == ["👑 Админ-панель"]


def test_menu_expired_subscription():
    state = SubscriptionState(telegram_id=1, active=False, expires_at=NOW - timedelta(days=2))
    text, _ = menu_view.render(1, "X", state)
    assert "🔴 Подписка истекла" in text
    assert "Нажми «Подписка», чтобы продлить доступ." in text


# --- Connect -----------------------------------------------------------


def test_connect_no_subscription_with_trial():
    text, kb = connect_view.no_subscription(trial_available=True, support_handle=None)
    assert text == (
        "🔒 <b>Подписка не активна</b>\n\nДля подключения к VPN нужна активная подписка.\n\n"
        "Попробуй бесплатно или выбери тариф ниже."
    )
    assert _keyboard_texts(kb) == [
        ["🎁 Попробовать 5 дней бесплатно"], ["💳 Подписка"],
        ["✍️ Написать администратору"], ["⬅️ В главное меню"],
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
        "🚀 <b>Ссылка для подключения VPN</b>\n\n<code>https://sub.example/abc</code>\n\n"
        "💡 <b>Как подключить:</b>\n<blockquote>\n1. Открой ссылку\n2. Скачай подходящий VPN клиент\n"
        "3. Импортируй ссылку подписки в клиент\n</blockquote>\n\n"
        "———\n🛡 <b>Обход блокировок</b> (12\xa0ГБ из 100\xa0ГБ)\n"
        "<blockquote>Отдельная ссылка, добавляется так же, как и основная. "
        "Включай обход, когда сайт заблокирован по мобильному интернету, "
        "и выключай, когда все работает штатно.</blockquote>\n\n"
        "<code>https://sub.example/obhod-abc</code>"
    )
    assert _keyboard_texts(kb) == [
        ["🔗 Открыть основную ссылку"], ["🛡 Открыть ссылку обхода"],
        ["➕ Нужно больше обхода"], ["📖 Как подключиться"], ["⬅️ В главное меню"],
    ]


def test_connect_success_non_pro_shows_obhod_upsell():
    state = SubscriptionState(telegram_id=1, active=True, plan_code="standard", subscription_url="https://sub.example/x")
    text, _ = connect_view.success(state)
    assert "Доступен в тарифе Pro" in text
    assert "<code>https://sub.example/x</code>" in text


def test_connect_success_pro_obhod_not_ready_yet():
    state = SubscriptionState(telegram_id=1, active=True, plan_code="pro", subscription_url="https://sub.example/x")
    text, _ = connect_view.success(state)
    assert "Готовим твою ссылку обхода" in text


def test_connect_error_has_no_dead_back_button():
    text, kb = connect_view.error(support_handle="dcfrq")
    assert "Не удалось получить ссылку" in text
    assert _keyboard_texts(kb) == [
        ["💳 Подписка"], ["✍️ Написать администратору"], ["⬅️ В главное меню"],
    ]


# --- Devices -------------------------------------------------------------


def test_devices_list_two_devices():
    devs = [
        DeviceInfo(hwid="aaaaaaaaaaaaaaaa11223344", platform="iOS", device_model="iPhone 15", updated_at=NOW),
        DeviceInfo(hwid="bbbbbbbbbbbbbbbb55667788", platform="Android", device_model="Xiaomi Redmi", updated_at=NOW - timedelta(days=14)),
    ]
    text, kb = devices_view.list_screen(devs, device_limit=5, unlink_enabled=True)

    assert text == (
        "📱 <b>Твои устройства</b> (2 из 5)\n\n"
        "📱 iPhone 15, последний раз онлайн: сегодня\n"
        "📱 Xiaomi Redmi, последний раз онлайн: 14\xa0дней назад\n\n"
        "Лишнее можно отвязать, освободится место под новое устройство."
    )
    assert _keyboard_texts(kb) == [
        ["❌ Отвязать: iPhone 15"], ["❌ Отвязать: Xiaomi Redmi"], ["⬅️ В главное меню"],
    ]


def test_devices_empty():
    text, kb = devices_view.list_screen([], device_limit=5, unlink_enabled=True)
    assert "Пока ни одно устройство не подключалось" in text
    assert _keyboard_texts(kb) == [["⬅️ В главное меню"]]


def test_devices_unlink_disabled_hides_unlink_buttons_but_keeps_the_list():
    devs = [DeviceInfo(hwid="a" * 16, platform="iOS", updated_at=NOW)]
    text, kb = devices_view.list_screen(devs, device_limit=5, unlink_enabled=False)
    assert "Твои устройства" in text and "1 из 5" in text
    assert _keyboard_texts(kb) == [["⬅️ В главное меню"]]


# --- Support / help ---------------------------------------------------------


def test_support_screen_with_privacy_link():
    text, kb = support_view.render(support_handle=None, privacy_url="https://example.com/privacy")
    assert text == (
        "ℹ️ <b>Справка по CRS VPN</b>\n\n🔐 <b>Что такое VPN?</b>\n"
        "<blockquote>VPN создает защищенное соединение между твоим устройством и интернетом.</blockquote>\n\n"
        "❓ <b>Частые вопросы</b>\n<blockquote>"
        "<b>VPN не подключается:</b> обнови подписку (кнопка «Обновить» в меню) и проверь, "
        "что импортирована свежая ссылка.\n\n"
        "<b>Сколько устройств можно подключить:</b> смотри в разделе «Мои устройства», лимит зависит от тарифа.\n\n"
        "<b>Как сменить устройство:</b> отвяжи старое в «Мои устройства» и подключи новое той же ссылкой."
        "</blockquote>\n\nНе нашел ответ? Напиши в поддержку."
    )
    assert _keyboard_texts(kb) == [
        ["✍️ Написать администратору"], ["📄 Оферта"], ["🔒 Политика конфиденциальности"], ["⬅️ В главное меню"],
    ]


def test_support_screen_without_privacy_link_hides_button():
    _, kb = support_view.render(support_handle="dcfrq", privacy_url=None)
    assert _keyboard_texts(kb) == [
        ["✍️ Написать администратору"], ["📄 Оферта"], ["⬅️ В главное меню"],
    ]
