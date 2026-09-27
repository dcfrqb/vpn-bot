"""Main menu view (release 3.0): type ``status`` (docs/SCREENS.md). Owner stream: D (User UI)."""
from __future__ import annotations

from app.bot.callbacks import Adm, Dev, Nav
from app.bot.views import kit
from app.domain.models import SubscriptionState
from app.domain.texts.menu import main_menu_screen
from app.domain.texts.ui import B


def render(
    telegram_id: int,
    name: str,
    state: SubscriptionState,
    *,
    is_admin: bool = False,
    trial_available: bool = False,
) -> kit.View:
    screen = main_menu_screen(telegram_id, name, state, trial_available=trial_available)
    return kit.view(
        screen,
        primary=[
            kit.action(B.CONNECT, Nav(s="connect")),
            kit.action(B.TRIAL, Nav(s="connect", p="trial")) if trial_available else None,
        ],
        secondary=[
            kit.action(B.SUBSCRIPTION, Nav(s="plans")),
            kit.action(B.DEVICES, Dev(a="list")),
            kit.pair(kit.action(B.REFRESH, Nav(s="main", p="refresh")), kit.action(B.HELP, Nav(s="help"))),
            kit.action(B.ADMIN_PANEL, Adm(s="panel", a="open")) if is_admin else None,
        ],
        footer=kit.Footer.none(),
    )
