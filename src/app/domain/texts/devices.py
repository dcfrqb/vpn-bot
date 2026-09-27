"""Texts: My devices list/unlink texts.

Owner stream: D (User UI), rendering DevicesService (B) data.
Plain module-level constants or small pure functions returning str.
No letter U+0451 (yo) in prose.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from app.domain.models import DeviceInfo
from app.domain.texts import h, ui
from app.domain.texts.ui import E

TITLE = "Мои устройства"

EMPTY_LINE = "Пока ни одно устройство не подключалось. Как только подключишься, оно появится здесь."
EMPTY = ui.items(TITLE, emoji=E.DEVICES, lines=[], empty=EMPTY_LINE).html()

# Toasts (callback alerts).
UNLINKED = ui.toast("Готово, устройство отвязано. Место освободилось, можно подключить новое.")
UNLINK_LIMIT = ui.toast("Отвязывать можно не чаще 3 раз в сутки. Попробуй позже.")

NOT_FOUND_SCREEN = ui.result("warn", "Устройство не найдено", "Список мог обновиться, открой его заново.")
UNLINK_NOT_FOUND = NOT_FOUND_SCREEN.html()


def _device_icon(platform: Optional[str]) -> str:
    p = (platform or "").lower()
    if "ios" in p or "iphone" in p or "ipad" in p or "mac" in p:
        return E.DEVICES if "mac" not in p else E.DEVICE_DESKTOP
    if "android" in p:
        return E.DEVICES
    if "windows" in p or "linux" in p:
        return E.DEVICE_DESKTOP
    return E.DEVICE_OTHER


def _last_seen(updated_at: Optional[datetime]) -> str:
    if updated_at is None:
        return "давно"
    dt = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc).date() - dt.date()).days
    if days <= 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    from app.domain.texts import days_ru

    return f"{days_ru(days)} назад"


def _device_line(dev: DeviceInfo) -> str:
    name = dev.device_model or dev.platform or "Неизвестное устройство"
    icon = _device_icon(dev.platform)
    return f"{icon} {h(name)}, последний раз онлайн: {_last_seen(dev.updated_at)}"


def list_screen(devices: Sequence[DeviceInfo], used: int, limit: Optional[int], *,
                unlink_enabled: bool = False) -> ui.Screen:
    if not devices:
        return ui.items(TITLE, emoji=E.DEVICES, lines=[], empty=EMPTY_LINE)
    counter = f"{h(used)} из {h(limit)}" if limit else h(used)
    # Review UX M3: promise unlinking only when the buttons are there.
    hint = ("Лишнее можно отвязать, освободится место под новое устройство." if unlink_enabled
            else "Чтобы освободить место под новое устройство, напиши в поддержку.")
    return ui.items(f"{TITLE} ({counter})", emoji=E.DEVICES,
                    lines=[_device_line(d) for d in devices], hint=hint)


def list_text(devices: Sequence[DeviceInfo], used: int, limit: Optional[int], *,
              unlink_enabled: bool = False) -> str:
    return list_screen(devices, used, limit, unlink_enabled=unlink_enabled).html()


def ask_unlink_screen(dev: DeviceInfo) -> ui.Screen:
    name = dev.device_model or dev.platform or "это устройство"
    return ui.confirm(f"Отвязать «{h(name)}»?", "Оно перестанет использовать VPN, пока не подключится заново.")


def ask_unlink(dev: DeviceInfo) -> str:
    return ask_unlink_screen(dev).html()
