"""«Мои устройства» view (release 3.0). Owner stream: D over B's DevicesService."""
from __future__ import annotations

from typing import Optional, Sequence

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import Dev
from app.bot.views import btn, kb
from app.bot.views.common import back_to_main_row
from app.domain.models import DeviceInfo
from app.domain.texts import devices as t


def list_screen(
    devices: Sequence[DeviceInfo],
    *,
    device_limit: Optional[int],
    unlink_enabled: bool,
) -> tuple[str, InlineKeyboardMarkup]:
    """Always shows the list (N of M); the unlink button per device is
    behind DEVICES_UNLINK_ENABLED."""
    text = t.list_text(devices, len(devices), device_limit)
    rows: list[list] = []
    if unlink_enabled:
        for dev in devices:
            name = (dev.device_model or dev.platform or dev.short_id)[:24]
            rows.append([btn(f"❌ Отвязать: {name}", Dev(a="ask", id=dev.short_id))])
    rows.append(back_to_main_row())
    return text, kb(rows)


def ask_unlink(dev: DeviceInfo) -> tuple[str, InlineKeyboardMarkup]:
    rows = [
        [btn("Да, отвязать", Dev(a="unlink", id=dev.short_id))],
        [btn("Отмена", Dev(a="list"))],
    ]
    return t.ask_unlink(dev), kb(rows)


def not_found() -> tuple[str, InlineKeyboardMarkup]:
    return t.UNLINK_NOT_FOUND, kb([[btn("К списку", Dev(a="list"))]])
