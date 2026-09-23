"""/start and bot commands (set_my_commands lives here).

Owner stream: D (User UI).
"""
from __future__ import annotations

from typing import Any

from aiogram import Router, types
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BotCommand

from app.domain.models import PromoOutcome
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


@router.message(CommandStart())
async def cmd_start(message: types.Message, **data) -> None:
    from app.bot.routers.menu import show_main_screen

    await _ensure_user(message.from_user)
    await show_main_screen(message, force=True, **data)


@router.message(Command("help"))
async def cmd_help(message: types.Message, **data) -> None:
    from app.bot.routers.support import show_help_screen

    await show_help_screen(message, **data)


@router.message(Command("devices"))
async def cmd_devices(message: types.Message, **data) -> None:
    from app.bot.routers.devices import show_devices_screen

    await show_devices_screen(message, **data)


_TRIAL_TEXT = {
    PromoOutcome.ALREADY_USED: "Пробный период уже был использован на этом аккаунте.",
    PromoOutcome.NOT_ELIGIBLE: "Пробный период сейчас недоступен для этого аккаунта.",
    PromoOutcome.EXHAUSTED: "Пробный период сейчас недоступен для этого аккаунта.",
    PromoOutcome.DISABLED: "Пробный период временно выключен.",
    PromoOutcome.BUSY: "Секунду, обрабатываем предыдущий запрос. Попробуй еще раз.",
    PromoOutcome.ERROR: "Не получилось включить пробный период, попробуй чуть позже.",
}


@router.message(Command("trial"))
async def cmd_trial(message: types.Message, **data) -> None:
    from app.bot.routers.connect import show_connect_screen

    promo = data.get("promo")
    status_service = data.get("status_service")
    user = message.from_user
    if promo is None:
        await message.answer("Пробный период временно недоступен, попробуй чуть позже.", parse_mode=None)
        return
    try:
        reward = await promo.start_trial(user.id)
    except Exception as e:  # noqa: BLE001 - PromoService may not be shipped yet (owner: E)
        logger.debug(f"cmd_trial: start_trial failed ({type(e).__name__})")
        await message.answer("Пробный период временно недоступен, попробуй чуть позже.", parse_mode=None)
        return

    if not reward.applied:
        text = _TRIAL_TEXT.get(reward.outcome, "Пробный период сейчас недоступен.")
        await message.answer(text, parse_mode=None)
        return

    if status_service is not None:
        try:
            await status_service.invalidate(user.id)
        except Exception:  # noqa: BLE001
            pass
    await message.answer("Пробный период включен на 5 дней!", parse_mode=None)
    await show_connect_screen(message, **data)


@router.message(Command("promo"))
async def cmd_promo(message: types.Message, command: CommandObject, **data) -> None:
    promo = data.get("promo")
    code = (command.args or "").strip()
    if not code:
        await message.answer(
            "Отправь код так: /promo КОД", parse_mode=None,
        )
        return
    if promo is None:
        await message.answer("Промокоды временно недоступны, попробуй чуть позже.", parse_mode=None)
        return
    try:
        reward = await promo.redeem(message.from_user.id, code, source="command")
    except Exception as e:  # noqa: BLE001 - PromoService may not be shipped yet (owner: E)
        logger.debug(f"cmd_promo: redeem failed ({type(e).__name__})")
        await message.answer("Промокоды временно недоступны, попробуй чуть позже.", parse_mode=None)
        return

    if reward.applied:
        await message.answer("Промокод применен, спасибо!", parse_mode=None)
        status_service = data.get("status_service")
        if status_service is not None:
            try:
                await status_service.invalidate(message.from_user.id)
            except Exception:  # noqa: BLE001
                pass
        from app.bot.routers.menu import show_main_screen

        await show_main_screen(message, force=True, **data)
        return

    outcome_text = {
        PromoOutcome.NOT_FOUND: "Такой промокод не найден.",
        PromoOutcome.EXPIRED: "Срок действия этого промокода истек.",
        PromoOutcome.EXHAUSTED: "Лимит использований этого промокода исчерпан.",
        PromoOutcome.ALREADY_USED: "Ты уже использовал этот промокод.",
        PromoOutcome.NOT_ELIGIBLE: "Этот промокод для тебя недоступен.",
        PromoOutcome.DISABLED: "Промокоды сейчас выключены.",
    }.get(reward.outcome, "Не получилось применить промокод, попробуй еще раз.")
    await message.answer(outcome_text, parse_mode=None)
