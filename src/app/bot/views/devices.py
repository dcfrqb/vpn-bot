"""«Мои устройства» view (release 3.0). Owner stream: D over B's DevicesService."""
from __future__ import annotations

from typing import Optional, Sequence

from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import Dev
from app.bot.views import btn, kb
from app.bot.callbacks import Nav
from app.bot.views.common import back_to_main_row, support_row
from app.domain.models import DeviceInfo
from app.domain.texts import devices as t
from app.domain.texts.common import BTN_SUBSCRIPTION


def list_screen(
    devices: Sequence[DeviceInfo],
    *,
    device_limit: Optional[int],
    unlink_enabled: bool,
    support_handle: Optional[str] = None,
    active: bool = True,
) -> tuple[str, InlineKeyboardMarkup]:
    """Always shows the list (N of M); the unlink button per device is
    behind DEVICES_UNLINK_ENABLED, otherwise the support button (review UX M3).
    No subscription: a way to the plans (m8)."""
    text = t.list_text(devices, len(devices), device_limit, unlink_enabled=unlink_enabled)
    rows: list[list] = []
    if unlink_enabled:
        for dev in devices:
            name = (dev.device_model or dev.platform or dev.short_id)[:24]
            rows.append([btn(f"❌ Отвязать: {name}", Dev(a="ask", id=dev.short_id))])
    elif devices:
        rows.append(support_row(support_handle))
    if not active:
        rows.append([btn(BTN_SUBSCRIPTION, Nav(s="plans"))])
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
