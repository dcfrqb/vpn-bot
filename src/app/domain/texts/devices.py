"""Texts: «Мои устройства» (list of device cards, unlink confirmation).

Owner stream: D (User UI), rendering DevicesService (B) data.
Layout: type ``items`` with one numbered card per device (docs/SCREENS.md).
No letter U+0451 (yo) in prose.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional, Sequence

from app.domain.models import DeviceInfo
from app.domain.texts import MSK, days_ru, fmt_date_msk, h, ui
from app.domain.texts.ui import E

TITLE = "Мои устройства"
STALE_DAYS = 30  # a device silent for longer gets the «давно не выходило на связь» mark
NAME_MAX = 32

EMPTY_LINE = "Пока ни одно устройство не подключалось. Как только подключишься, оно появится здесь."
EMPTY = ui.items(TITLE, emoji=E.DEVICES, lines=[], empty=EMPTY_LINE).html()

HINT_UNLINK = ("Нажми ❌ с номером устройства, чтобы отвязать его. Место освободится сразу, а если "
               "устройство снова подключится, приложение добавит его заново.")
HINT_SUPPORT = "Чтобы освободить место под новое устройство, напиши в поддержку."

# Toasts (callback alerts).
UNLINKED = ui.toast("Готово, устройство отвязано. Место освободилось, можно подключить новое.")
UNLINK_LIMIT = ui.toast("Отвязывать можно не чаще 3 раз в сутки. Попробуй позже.")

NOT_FOUND_SCREEN = ui.result("warn", "Устройство не найдено", "Список мог обновиться, открой его заново.")
UNLINK_NOT_FOUND = NOT_FOUND_SCREEN.html()

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# ----------------------------------------------------------------- what the panel tells us

_PLATFORMS = {
    "ios": "iOS", "ipados": "iPadOS", "android": "Android", "windows": "Windows", "macos": "macOS",
    "mac": "macOS", "darwin": "macOS", "linux": "Linux", "tvos": "tvOS", "androidtv": "Android TV",
}
# Client apps as they name themselves in the User-Agent (first token), lowercased -> display name.
_APPS = {
    "happ": "Happ", "v2raytun": "v2RayTun", "v2box": "V2Box", "hiddify": "Hiddify", "hiddifynext": "Hiddify",
    "karing": "Karing", "streisand": "Streisand", "shadowrocket": "Shadowrocket", "foxray": "FoXray",
    "flclash": "FlClash", "clash-verge": "Clash Verge", "clashmi": "Clash Mi", "clash-meta": "Clash Meta",
    "clashx": "ClashX", "nekobox": "NekoBox", "nekoray": "NekoRay", "sing-box": "sing-box", "singbox": "sing-box",
    "v2rayng": "v2rayNG", "v2rayn": "v2rayN", "incy": "INCY", "koala-clash": "Koala Clash", "throne": "Throne",
    "prizrak-box": "Prizrak-Box", "exclave": "Exclave", "husi": "Husi",
}
# Generic HTTP stacks: not an app name.
_NOT_APPS = {"mozilla", "okhttp", "dart", "cfnetwork", "darwin", "go-http-client", "python-requests", "curl",
             "dalvik", "java", "axios", "node", "undici", "electron"}
_APP_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,23}$")


def platform_title(platform: Optional[str]) -> str:
    p = (platform or "").strip()
    return _PLATFORMS.get(p.lower().replace(" ", ""), p)


def os_line(dev: DeviceInfo) -> str:
    """``iOS 18.1``: platform and OS version, whatever of it is known."""
    return " ".join(x for x in (platform_title(dev.platform), (dev.os_version or "").strip()) if x)


def app_title(user_agent: Optional[str]) -> str:
    """Client app from the User-Agent: ``Happ/3.1.0/ios CFNetwork/...`` -> ``Happ 3.1``."""
    token = (user_agent or "").strip().split(" ", 1)[0]
    raw, _, rest = token.partition("/")
    if not _APP_NAME.match(raw) or raw.lower() in _NOT_APPS:
        return ""
    name = _APPS.get(raw.lower(), raw)
    ver = rest.split("/", 1)[0].lstrip("vV")
    parts = ver.split(".")
    # build numbers (Shadowrocket/2070) say nothing to a user: only dotted versions
    ver = ".".join(parts[:2]) if len(parts) > 1 and all(p.isdigit() for p in parts[:2]) else ""
    return f"{name} {ver}".strip()


def display_name(dev: DeviceInfo, n: int) -> str:
    """One name for the card, its button and the confirmation (plain text, not escaped):
    model, else OS/platform, else app, else «Устройство N»."""
    name = (dev.device_model or "").strip() or os_line(dev) or app_title(dev.user_agent) or f"Устройство {int(n)}"
    return name if len(name) <= NAME_MAX else name[: NAME_MAX - 1] + "…"


def ordered(devices: Sequence[DeviceInfo]) -> list[DeviceInfo]:
    """Cards and their numbers follow this order: last seen first."""
    return sorted(devices, key=lambda d: d.updated_at or d.created_at or _EPOCH, reverse=True)


def number_of(devices: Sequence[DeviceInfo], short_id: str) -> Optional[tuple[int, DeviceInfo]]:
    for i, d in enumerate(ordered(devices), 1):
        if d.short_id == short_id:
            return i, d
    return None


# ----------------------------------------------------------------- cards


def _days_ago(dt: Optional[datetime]) -> Optional[int]:
    if dt is None:
        return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc).astimezone(MSK).date() - dt.astimezone(MSK).date()).days)


def last_seen(dt: Optional[datetime]) -> str:
    days = _days_ago(dt)
    if days is None:
        return "давно"
    if days == 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days_ru(days)} назад"


def _device_icon(platform: Optional[str]) -> str:
    p = (platform or "").lower()
    if "mac" in p or "windows" in p or "linux" in p or "darwin" in p:
        return E.DEVICE_DESKTOP
    if "ios" in p or "iphone" in p or "ipad" in p or "android" in p:
        return E.DEVICES
    return E.DEVICE_OTHER


def device_card(dev: DeviceInfo, n: int) -> Optional[ui.Block]:
    name = display_name(dev, n)
    osl, app = os_line(dev), app_title(dev.user_agent)
    about = [x for x in (osl, app) if x and x != name]
    seen = dev.updated_at or dev.created_at
    days = _days_ago(seen)
    return ui.card(
        n, h(name),
        h(" · ".join(about)) if about else None,
        ui.field("Добавлено", fmt_date_msk(dev.created_at)) if dev.created_at else None,
        f"Онлайн: {last_seen(seen)}",
        f"{E.WARN} Давно не выходило на связь" if days is None or days > STALE_DAYS else None,
        emoji=_device_icon(dev.platform),
    )


def list_screen(devices: Sequence[DeviceInfo], used: int, limit: Optional[int], *,
                unlink_enabled: bool = False) -> ui.Screen:
    if not devices:
        return ui.items(TITLE, emoji=E.DEVICES, lines=[], empty=EMPTY_LINE)
    counter = f"{h(used)} из {h(limit)}" if limit else h(used)
    # Review UX M3: promise unlinking only when the buttons are there.
    return ui.items(f"{TITLE} ({counter})", emoji=E.DEVICES,
                    cards=[device_card(d, i) for i, d in enumerate(ordered(devices), 1)],
                    hint=HINT_UNLINK if unlink_enabled else HINT_SUPPORT)


def list_text(devices: Sequence[DeviceInfo], used: int, limit: Optional[int], *,
              unlink_enabled: bool = False) -> str:
    return list_screen(devices, used, limit, unlink_enabled=unlink_enabled).html()


def ask_unlink_screen(dev: DeviceInfo, n: int = 1) -> ui.Screen:
    return ui.confirm(
        f"Отвязать {h(display_name(dev, n))}?",
        "Место освободится сразу. Если это устройство снова подключится, приложение добавит его заново.",
    )


def ask_unlink(dev: DeviceInfo, n: int = 1) -> str:
    return ask_unlink_screen(dev, n).html()
