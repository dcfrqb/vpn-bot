"""«Мои устройства» view (release 3.0): ``items``, ``confirm``, ``result``.

Owner stream: D over B's DevicesService. Layout rules: docs/SCREENS.md.
"""
from __future__ import annotations

from typing import Optional, Sequence

from app.bot.callbacks import Dev, Nav
from app.bot.views import kit
from app.domain.models import DeviceInfo
from app.domain.texts import devices as t
from app.domain.texts.common import support_url
from app.domain.texts.ui import B


def list_screen(
    devices: Sequence[DeviceInfo],
    *,
    device_limit: Optional[int],
    unlink_enabled: bool,
    support_handle: Optional[str] = None,
    active: bool = True,
) -> kit.View:
    """Always shows the cards (N of M); numbered unlink buttons ``❌ n`` (n = card number)
    are behind DEVICES_UNLINK_ENABLED, otherwise the support button (review UX M3).
    No subscription: a way to the plans (m8)."""
    screen = t.list_screen(devices, len(devices), device_limit, unlink_enabled=unlink_enabled)
    options = []
    if unlink_enabled:
        options = kit.grid([kit.action(B.UNLINK_N.format(n=i), Dev(a="ask", id=dev.short_id))
                            for i, dev in enumerate(t.ordered(devices), 1)])
    return kit.view(
        screen,
        options=options,
        secondary=[kit.action(B.SUBSCRIPTION, Nav(s="plans")) if not active else None],
        links=[kit.link(B.SUPPORT, support_url(support_handle)) if devices and not unlink_enabled else None],
        footer=kit.Footer.to_menu(),
    )


def ask_unlink(dev: DeviceInfo, n: int = 1) -> kit.View:
    """``n``: the card number, so a nameless device is «Устройство n» here too."""
    return kit.view(
        t.ask_unlink_screen(dev, n),
        primary=[kit.pair(kit.action(f"{B.YES_PREFIX}, отвязать", Dev(a="unlink", id=dev.short_id)),
                          kit.action(B.CANCEL, Dev(a="list")))],
    )


def not_found() -> kit.View:
    return kit.view(t.NOT_FOUND_SCREEN, footer=kit.Footer.back_menu(Dev(a="list")))
