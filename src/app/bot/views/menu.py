"""Main menu view (release 3.0): type ``status`` (docs/SCREENS.md). Owner stream: D (User UI)."""
from __future__ import annotations

from app.bot.callbacks import Adm, Dev, Nav
from app.bot.views import kit
from app.domain.models import SubscriptionState
from app.domain.texts.menu import main_menu_screen
from app.domain.texts.common import support_url
from app.domain.texts.ui import B


def render(
    telegram_id: int,
    name: str,
    state: SubscriptionState,
    *,
    is_admin: bool = False,
    trial_available: bool = False,
    support_handle: str | None = None,
) -> kit.View:
    screen = main_menu_screen(telegram_id, name, state, trial_available=trial_available)
    return kit.view(
        screen,
        primary=[
            kit.action(B.CONNECT, Nav(s="connect")),
            kit.action(B.TRIAL, Nav(s="connect", p="trial")) if trial_available and not state.disabled and not state.stale else None,
        ],
        secondary=[
            kit.action(B.SUBSCRIPTION, Nav(s="plans")) if not state.disabled else None,
            kit.action(B.DEVICES, Dev(a="list")),
            kit.pair(kit.action(B.REFRESH, Nav(s="main", p="refresh")), kit.action(B.HELP, Nav(s="help"))),
            kit.action(B.ADMIN_PANEL, Adm(s="panel", a="open")) if is_admin else None,
        ],
        links=[kit.link(B.SUPPORT, support_url(support_handle))
               if state.disabled or state.stale or (state.active and (state.plan_code or "").lower() == "pro"
                                                    and state.obhod_readiness != "ready") else None],
        footer=kit.Footer.none(),
    )
