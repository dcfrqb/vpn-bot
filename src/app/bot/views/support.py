"""Help / support screen view (release 3.0): ``article``. Owner stream: D (User UI)."""
from __future__ import annotations

from typing import Optional

from app.bot.views import kit
from app.domain.texts.common import OFFER_URL, PRIVACY_URL, help_screen, support_url
from app.domain.texts.ui import B


def render(*, support_handle: Optional[str] = None, privacy_url: Optional[str] = None,
           offer_url: Optional[str] = None, unlink_enabled: bool = False) -> kit.View:
    return kit.view(
        help_screen(unlink_enabled),
        links=[
            kit.link(B.SUPPORT, support_url(support_handle)),
            kit.link(B.OFFER, offer_url or OFFER_URL),
            kit.link(B.PRIVACY, privacy_url or PRIVACY_URL),
        ],
        footer=kit.Footer.to_menu(),
    )
