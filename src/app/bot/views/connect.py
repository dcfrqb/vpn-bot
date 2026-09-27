"""Connect screen view (release 3.0): ``article`` on success, ``result`` otherwise.

Owner stream: D (User UI). Layout rules: docs/SCREENS.md.
"""
from __future__ import annotations

from typing import Optional

from app.bot.callbacks import Nav
from app.bot.views import kit
from app.domain.models import SubscriptionState
from app.domain.plans import OBHOD_PACKAGE_CODES, is_obhod_package_purchasable
from app.domain.texts import connect as t
from app.domain.texts.common import support_url
from app.domain.texts.ui import B

REFRESH = Nav(s="connect", p="refresh")


def loading() -> kit.View:
    return kit.View(t.LOADING, kit.keyboard())


def _support(handle: Optional[str]):
    return kit.link(B.SUPPORT, support_url(handle))


def error(support_handle: Optional[str] = None) -> kit.View:
    """Panel unreachable or no link for a live subscription (review UX M1):
    never the «not active, buy» screen for a paying user."""
    return kit.view(t.ERROR_SCREEN, primary=[kit.action(B.REFRESH, REFRESH)], links=[_support(support_handle)],
                    footer=kit.Footer.to_menu())


def no_subscription(*, trial_available: bool, support_handle: Optional[str] = None) -> kit.View:
    screen = t.NO_SUBSCRIPTION_SCREEN if trial_available else t.NO_SUBSCRIPTION_NO_TRIAL_SCREEN
    return kit.view(
        screen,
        primary=[kit.action(B.TRIAL, Nav(s="connect", p="trial")) if trial_available else None,
                 kit.action(B.SUBSCRIPTION, Nav(s="plans"))],
        links=[_support(support_handle)],
        footer=kit.Footer.to_menu(),
    )


def success(state: SubscriptionState, *, article_url: Optional[str] = None) -> kit.View:
    url = state.subscription_url or ""
    is_pro = state.plan_code == "pro"
    obhod_ready = bool(state.obhod_active and state.obhod_subscription_url)
    return kit.view(
        t.success_screen(state),
        primary=[kit.link(B.OPEN_LINK, url) if url else None],
        secondary=[
            kit.link(B.OPEN_OBHOD, state.obhod_subscription_url) if is_pro and obhod_ready else None,
            kit.action(B.OBHOD_MORE, Nav(s="plans", p="obhod"))
            if is_pro and any(is_obhod_package_purchasable(c) for c in OBHOD_PACKAGE_CODES) else None,
            kit.action(B.REFRESH, REFRESH) if is_pro and not obhod_ready else None,  # «нажми «Обновить»»
        ],
        links=[kit.link(B.ARTICLE, article_url) if article_url else None],
        footer=kit.Footer.to_menu(),
    )
