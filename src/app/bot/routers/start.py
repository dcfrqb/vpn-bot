"""/start and bot commands (set_my_commands lives here).

Owner stream: D (User UI). /trial, /promo, /solokhin, /sun718 and /friend
belong to stream E (``app.bot.routers.trial_promo``, registered earlier):
the promo engine owns the action, D's screens only call ``promo`` through DI.
"""
from __future__ import annotations

from typing import Any

from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand

from app.logger import logger

router = Router(name="r3_start")

BOT_COMMANDS: tuple[BotCommand, ...] = (
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="trial", description="Попробовать 5 дней бесплатно"),
    BotCommand(command="help", description="Помощь и поддержка"),
    BotCommand(command="devices", description="Мои устройства"),
    BotCommand(command="promo", description="Ввести промокод"),
)


async def set_my_commands(bot: Any) -> None:
    """Registers the bot command list shown in the Telegram UI. Called once
    from app.main.setup_dispatcher after the dispatcher is built."""
    try:
        await bot.set_my_commands(list(BOT_COMMANDS))
    except Exception as e:  # noqa: BLE001 - never block startup on this
        logger.warning(f"set_my_commands failed: {type(e).__name__}: {e}")


async def _ensure_user(user: types.User) -> None:
    from app.services.users import get_or_create_telegram_user

    try:
        await get_or_create_telegram_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
        )
    except Exception as e:  # noqa: BLE001 - soft-fail, matches 2.x cmd_start
        logger.warning(f"r3_start: get_or_create_telegram_user soft-fail tg_id={user.id}: {type(e).__name__}")


async def _reset_broadcast_opt_out(user_id: int) -> None:
    """2.x behaviour: an explicit /start re-subscribes the user to broadcasts."""
    try:
        from app.services.broadcast import set_opt_out

        await set_opt_out(user_id, False)
    except Exception as e:  # noqa: BLE001 - never block /start on this
        logger.debug(f"r3_start: reset opt-out failed tg_id={user_id}: {type(e).__name__}")


@router.message(CommandStart())
async def cmd_start(message: types.Message, **data) -> None:
    from app.bot.routers.menu import show_main_screen

    await _ensure_user(message.from_user)
    await _reset_broadcast_opt_out(message.from_user.id)
    await show_main_screen(message, force=True, **data)


@router.message(Command("help"))
async def cmd_help(message: types.Message, **data) -> None:
    from app.bot.routers.support import show_help_screen

    await show_help_screen(message, **data)


@router.message(Command("devices"))
async def cmd_devices(message: types.Message, **data) -> None:
    from app.bot.routers.devices import show_devices_screen

    await show_devices_screen(message, **data)
