"""Texts: My devices list/unlink texts.

Owner stream: D (User UI), rendering DevicesService (B) data.
Plain module-level constants or small pure functions returning str.
No letter U+0451 (yo) in prose.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from app.domain.models import DeviceInfo
from app.domain.texts import h

TITLE_ICON = "📱"

EMPTY = (
    "📱 <b>Твои устройства</b>\n\n"
    "Пока ни одно устройство не подключалось. Как только подключишься, "
    "оно появится здесь."
)

UNLINKED = "Готово, устройство отвязано. Место освободилось, можно подключить новое."

UNLINK_LIMIT = "Отвязывать можно не чаще 3 раз в сутки. Попробуй позже."

UNLINK_NOT_FOUND = "Это устройство уже не найдено, список мог обновиться."


def _device_icon(platform: Optional[str]) -> str:
    p = (platform or "").lower()
    if "ios" in p or "iphone" in p or "ipad" in p or "mac" in p:
        return "📱" if "mac" not in p else "💻"
    if "android" in p:
        return "📱"
    if "windows" in p or "linux" in p:
        return "💻"
    return "📶"


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


def list_text(devices: Sequence[DeviceInfo], used: int, limit: Optional[int], *,
              unlink_enabled: bool = False) -> str:
    if not devices:
        return EMPTY
    limit_str = h(limit) if limit else "без лимита"
    lines = [f"📱 <b>Твои устройства</b> ({h(used)} из {limit_str})", ""]
    lines.extend(_device_line(d) for d in devices)
    lines.append("")
    # Review UX M3: promise unlinking only when the buttons are there.
    lines.append("Лишнее можно отвязать, освободится место под новое устройство." if unlink_enabled
                 else "Чтобы освободить место под новое устройство, напиши в поддержку.")
    return "\n".join(lines)


def ask_unlink(dev: DeviceInfo) -> str:
    name = dev.device_model or dev.platform or "это устройство"
    return f"Отвязать «{h(name)}»? Оно перестанет использовать VPN, пока не подключится заново."
