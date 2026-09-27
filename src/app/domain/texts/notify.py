"""Texts: Reminders, expiry/grace, panel event notices to users and admins.

Owner stream: C (Panel events).
Plain module-level constants or small pure functions returning str.
Use helpers from app.domain.texts (h, plural_ru, fmt_date_msk, fmt_rub).
No letter U+0451 (yo) in prose. Drafts: ТЕКСТЫ_3.0.md section 4.
User texts are push screens (HTML, values escaped here); admin texts are plain text.
"""
from __future__ import annotations

from typing import Optional

from app.domain.texts import days_ru, devices_ru, fmt_date_msk, fmt_gb, h, ui
from app.domain.texts.ui import B, E

# --------------------------------------------------------------- buttons

BTN_RENEW = B.RENEW
BTN_OBHOD_PACKAGES = B.OBHOD_MORE
BTN_CONNECT = B.CONNECT
BTN_DEVICES = B.DEVICES
BTN_ARTICLE = B.ARTICLE

# --------------------------------------------------------------- reminders (type: push)

_RENEW_HINT = "Продлить можно в любой момент, настройки в приложении менять не придется."


def reminder_screen(window: str, expires_at=None) -> ui.Screen:
    """window: "3d" | "1d" | "0d" | "a1d" (day after expiry)."""
    if window == "3d":
        return ui.push("warn", "Подписка закончится через 3 дня",
                       ui.field("Дата окончания", fmt_date_msk(expires_at)),
                       hint="Продли сейчас, чтобы не остаться без VPN. Это займет минуту.")
    if window == "1d":
        return ui.push("warn", "Подписка заканчивается завтра",
                       hint="Продли сейчас, чтобы доступ не прервался.")
    if window == "0d":
        return ui.push("warn", "Сегодня последний день подписки", "Потом доступ отключится.",
                       hint="Продли, если хочешь остаться на связи.")
    return ui.push("error", "Подписка закончилась вчера", "Доступ отключен.", hint=_RENEW_HINT)


def reminder_text(window: str, expires_at=None) -> str:
    return reminder_screen(window, expires_at).html()


REMIND_1D = reminder_text("1d")
REMIND_0D = reminder_text("0d")
REMIND_AFTER_1D = reminder_text("a1d")

# --------------------------------------------------------------- grace


def grace_started_screen(days: int, until, daily_gb: int) -> ui.Screen:
    return ui.push(E.GRACE, "Льготный период",
                   f"Подписка закончилась, но мы оставили доступ еще на {days_ru(days)}, чтобы ты успел продлить.",
                   ui.field("Доступ до", fmt_date_msk(until, with_time=True)) + " (МСК)",
                   f"Работает часть серверов и до {int(daily_gb)} ГБ трафика в сутки.",
                   hint="Продли, чтобы VPN не отключился.")


def grace_started(days: int, until, daily_gb: int) -> str:
    return grace_started_screen(days, until, daily_gb).html()


GRACE_ENDED_SCREEN = ui.push("error", "Льготный период закончился", "Доступ к VPN отключен.", hint=_RENEW_HINT)
GRACE_ENDED = GRACE_ENDED_SCREEN.html()

# --------------------------------------------------------------- panel events


def device_added_screen(model: Optional[str], used: Optional[int], limit: Optional[int],
                        support: Optional[str]) -> ui.Screen:
    if used is not None and limit:
        places = f"Занято {h(used)} из {h(limit)} мест под устройства."
    elif limit:
        places = f"Лимит на твоем тарифе: {devices_ru(limit)}."
    else:
        places = None
    who = h(support) if support else "в поддержку"
    return ui.push(E.DEVICES, "Новое устройство",
                   ui.field("Устройство", model) if model else "К подписке подключилось новое устройство.",
                   places,
                   hint=f"Если это не ты, напиши {who}, разберемся.")


def device_added(model: Optional[str], used: Optional[int], limit: Optional[int], support: Optional[str]) -> str:
    return device_added_screen(model, used, limit, support).html()


NOT_CONNECTED_SCREEN = ui.push(E.CONNECT, "VPN еще не подключен",
                               "Это делается за пару минут: поставь приложение и добавь в него свою ссылку.",
                               hint="Нажми кнопку ниже, там ссылка и шаги.")
NOT_CONNECTED = NOT_CONNECTED_SCREEN.html()


def obhod_limited_screen(limit_bytes: Optional[int], can_buy: bool) -> ui.Screen:
    return ui.push(E.OBHOD, "Трафик обхода закончился",
                   ui.field("Лимит на месяц", fmt_gb(limit_bytes)) if limit_bytes else None,
                   "Основная ссылка работает как обычно.",
                   hint=("Если обход нужен сейчас, можно докупить пакет трафика." if can_buy
                         else "Лимит обновится в начале следующего месяца."))


def obhod_limited(limit_bytes: Optional[int], can_buy: bool) -> str:
    return obhod_limited_screen(limit_bytes, can_buy).html()


# --------------------------------------------------------------- maintenance

# Toast (callback alerts are limited to 200 characters).
MAINTENANCE_SCREEN = ui.toast(
    "Идут технические работы, эта функция временно недоступна. "
    "VPN у тебя продолжает работать. Попробуй через 10-15 минут."
)
MAINTENANCE_CHECKOUT_SCREEN = ui.push(
    "info", "Идут технические работы",
    "Оплата работает: если доступ не появится сразу, бот выдаст его сам, как только работы закончатся.",
)
MAINTENANCE_CHECKOUT_NOTICE = MAINTENANCE_CHECKOUT_SCREEN.html()

# --------------------------------------------------------------- admins (plain text)

def admin_node_lost(name: str, address: str, message: Optional[str]) -> str:
    """HTML, through the screen kit (``ui.admin_alert``); the caller passes html=True."""
    lines = [ui.field("Нода", f"{name} ({address})")]
    if message:
        lines.append(ui.field("Причина", message))
    return ui.admin_alert("Нода недоступна", emoji="🔴", lines=lines).html()


def admin_node_restored(name: str, address: str) -> str:
    return ui.admin_alert("Нода снова на связи", emoji="🟢", lines=[ui.field("Нода", f"{name} ({address})")]).html()


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
