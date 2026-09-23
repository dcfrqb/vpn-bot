"""Texts: Reminders, expiry/grace, panel event notices to users and admins.

Owner stream: C (Panel events).
Plain module-level constants or small pure functions returning str.
Use helpers from app.domain.texts (h, plural_ru, fmt_date_msk, fmt_rub).
No letter U+0451 (yo) in prose. Drafts: ТЕКСТЫ_3.0.md section 4.
All user texts are plain text (sent with html=False, the Notifier escapes).
"""
from __future__ import annotations

from typing import Optional

from app.domain.texts import days_ru, devices_ru, fmt_date_msk, fmt_gb
from app.domain.texts import common as _c

# --------------------------------------------------------------- buttons

BTN_RENEW = "💳 Продлить подписку"
BTN_OBHOD_PACKAGES = "➕ Докупить трафик обхода"
# Shared vocabulary (review UX M4)
BTN_CONNECT = _c.BTN_CONNECT
BTN_DEVICES = _c.BTN_DEVICES
BTN_ARTICLE = _c.BTN_ARTICLE

# --------------------------------------------------------------- reminders

REMIND_3D = (
    "Подписка CRS VPN закончится через 3 дня ({date}). Чтобы не остаться без VPN, "
    "продли сейчас, это займет минуту."
)
REMIND_1D = "Подписка заканчивается завтра. Продли сейчас, чтобы доступ не прервался."
REMIND_0D = (
    "Сегодня последний день подписки CRS VPN. Потом доступ отключится. "
    "Продли, если хочешь остаться на связи."
)
REMIND_AFTER_1D = (
    "Подписка закончилась вчера, доступ отключен. Продлить можно в любой момент, "
    "старые настройки в приложении менять не придется."
)


def reminder_text(window: str, expires_at=None) -> str:
    """window: "3d" | "1d" | "0d" | "a1d" (day after expiry)."""
    if window == "3d":
        return REMIND_3D.format(date=fmt_date_msk(expires_at))
    if window == "1d":
        return REMIND_1D
    if window == "0d":
        return REMIND_0D
    return REMIND_AFTER_1D


# --------------------------------------------------------------- grace

def grace_started(days: int, until, daily_gb: int) -> str:
    return (
        f"Подписка закончилась, но мы оставили доступ еще на {days_ru(days)} "
        f"(до {fmt_date_msk(until, with_time=True)} по Москве), чтобы ты успел продлить. "
        f"В эти дни работает часть серверов и до {daily_gb} ГБ трафика в сутки. "
        "После этого VPN отключится."
    )


GRACE_ENDED = (
    "Льготный период закончился, доступ к VPN отключен. Продлить можно в любой момент, "
    "настройки в приложении менять не придется."
)

# --------------------------------------------------------------- panel events

def device_added(model: Optional[str], used: Optional[int], limit: Optional[int], support: Optional[str]) -> str:
    what = f": {model}" if model else ""
    lines = [f"К твоей подписке подключилось новое устройство{what}."]
    if used is not None and limit:
        lines.append(f"Занято {used} из {limit} мест под устройства.")
    elif limit:
        lines.append(f"Лимит на твоем тарифе: {devices_ru(limit)}.")
    if support:
        lines.append(f"Если это не ты: напиши {support}, разберемся.")
    else:
        lines.append("Если это не ты: напиши в поддержку, разберемся.")
    return "\n".join(lines)


NOT_CONNECTED = (
    "Похоже, VPN еще ни разу не подключался. Это делается за пару минут: "
    "поставь приложение и добавь в него свою ссылку. Нажми кнопку ниже, там ссылка и шаги."
)


def obhod_limited(limit_bytes: Optional[int], can_buy: bool) -> str:
    cap = f" ({fmt_gb(limit_bytes)})" if limit_bytes else ""
    text = f"Трафик ссылки «обход» на этот месяц закончился{cap}. Основная ссылка работает как обычно."
    if can_buy:
        text += " Если обход нужен сейчас, можно докупить пакет трафика."
    else:
        text += " Лимит обновится в начале следующего месяца."
    return text


# --------------------------------------------------------------- maintenance

# Alert text (callback alerts are limited to 200 characters).
MAINTENANCE_SCREEN = (
    "Идут технические работы, эта функция временно недоступна. "
    "VPN у тебя продолжает работать. Попробуй через 10-15 минут."
)
MAINTENANCE_CHECKOUT_NOTICE = (
    "Сейчас идут технические работы. Оплата работает: если доступ не появится сразу, "
    "бот выдаст его сам, как только работы закончатся."
)

# --------------------------------------------------------------- admins (plain text)

def admin_node_lost(name: str, address: str, message: Optional[str]) -> str:
    tail = f"\nПричина: {message}" if message else ""
    return f"Нода {name} ({address}) недоступна.{tail}"


def admin_node_restored(name: str, address: str) -> str:
    return f"Нода {name} ({address}) снова на связи."


def admin_panel_down(fails: int, auto: bool) -> str:
    tail = " Включен режим техработ." if auto else " Режим техработ не включался (MAINTENANCE_AUTO_ENABLED выключен)."
    return f"Панель Remnawave не отвечает ({fails} проверки подряд).{tail}"


ADMIN_PANEL_UP = "Панель Remnawave снова отвечает. Автоматический режим техработ снят."


def admin_maintenance_state(active: bool, reason: Optional[str], auto_enabled: bool) -> str:
    state = "ВКЛЮЧЕН" if active else "выключен"
    lines = [f"Режим техработ: {state}."]
    if active and reason:
        lines.append(f"Причина: {reason}")
    lines.append(
        "Автовключение по проверке панели: " + ("да" if auto_enabled else "нет (MAINTENANCE_AUTO_ENABLED)")
    )
    lines.append("Пока режим включен, пользователям закрыты пробный период, подключение и устройства. Оплата работает.")
    return "\n".join(lines)


BTN_MAINT_ON = "Включить техработы"
BTN_MAINT_OFF = "Выключить техработы"


def admin_grace_started(telegram_id: int, until) -> str:
    return f"Льготный период: юзер {telegram_id} до {fmt_date_msk(until, with_time=True)}."


def admin_webhook_error(event: str, error_type: str) -> str:
    return f"Вебхук панели {event}: ошибка обработки ({error_type})."
