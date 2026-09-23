"""Main menu view (release 3.0). Owner stream: D (User UI)."""
from __future__ import annotations


from aiogram.types import InlineKeyboardMarkup

from app.bot.callbacks import Adm, Dev, Nav
from app.bot.views import btn, kb
from app.domain.models import SubscriptionState
from app.domain.texts.common import (
    BTN_ADMIN_PANEL,
    BTN_CONNECT,
    BTN_DEVICES,
    BTN_HELP,
    BTN_REFRESH,
    BTN_SUBSCRIPTION,
    BTN_TRIAL,
)
from app.domain.texts.menu import main_menu_text


def render(
    telegram_id: int,
    name: str,
    state: SubscriptionState,
    *,
    is_admin: bool = False,
    trial_available: bool = False,
) -> tuple[str, InlineKeyboardMarkup]:
    text = main_menu_text(telegram_id, name, state, trial_available=trial_available)

    rows: list[list] = [[btn(BTN_CONNECT, Nav(s="connect"))]]
    if trial_available:
        rows.append([btn(BTN_TRIAL, Nav(s="connect", p="trial"))])
    rows.append([btn(BTN_SUBSCRIPTION, Nav(s="plans"))])
    rows.append([btn(BTN_DEVICES, Dev(a="list"))])
    rows.append([btn(BTN_REFRESH, Nav(s="main", p="refresh")), btn(BTN_HELP, Nav(s="help"))])
    if is_admin:
        rows.append([btn(BTN_ADMIN_PANEL, Adm(s="panel", a="open"))])
    return text, kb(rows)
