"""Deep links /start <code> and /start g_<code> (promo codes, gifts). Must stay right after site_login and before start.

Owner stream: E (Growth & admin).

- ``/start login_*`` is never taken here (site_login keeps priority even if
  its own filter changes);
- ``/start trial|solokhin|sun718`` (when the code's flag is on),
  ``/start g_<token>`` (GIFTS_ENABLED) and ``/start <code>`` for an existing
  promo code (PROMO_CODES_ENABLED) are redeemed here; any other payload
  falls through to the start router (stream D).

User-side promo commands live in app.bot.routers.trial_promo (next router).
"""
from __future__ import annotations

from typing import Any

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message

from app.bot.routers.trial_promo import redeem_and_reply
from app.services.promo import get_promo

router = Router(name="r3_promo_deeplink")



async def is_promo_link(message: Message, command: CommandObject, container: Any = None) -> bool:
    args = (command.args or "").strip()
    if not args or args.startswith("login_") or container is None:
        return False
    return await get_promo(container).is_known_code(args)


@router.message(CommandStart(deep_link=True), is_promo_link)
async def start_with_code(message: Message, command: CommandObject, container: Any) -> None:
    await redeem_and_reply(message, command.args.strip(), container, source="deeplink")
