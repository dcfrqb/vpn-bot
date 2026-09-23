"""Admin: obhod control (Adm s=obhod, /obhod <telegram_id>). Owner: E.

Card of a user's obhod subscription (traffic, cap, package) with actions:
grant a package (250/500 GB for its period), back to the base cap, switch
obhod off (with confirmation). Admins only (AdminGuard).
"""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import Adm
from app.bot.middlewares.admin_guard import guard_router
from app.bot.views import admin as V
from app.bot.views import render
from app.domain.plans import OBHOD_PACKAGE_CATALOG
from app.domain.texts import admin as T
from app.logger import logger
from app.services.grants import ObhodAdmin

router = guard_router(Router(name="r3_admin_obhod"))
obhod = ObhodAdmin()
PACKAGES = tuple((code, meta["display"]) for code, meta in OBHOD_PACKAGE_CATALOG.items())


async def _card(event: Any, tg: int, *, answered: bool = False) -> None:
    try:
        info = await obhod.info(tg)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"admin obhod: info failed tg={tg} ({type(e).__name__})")
        await render(event, f"Не получилось прочитать обход {tg}, панель или БД недоступны.", answer_callback=not answered)
        return
    await render(event, *V.obhod_card(info, PACKAGES), answer_callback=not answered)


@router.message(Command("obhod"))
async def cmd_obhod(message: Message, command: CommandObject) -> None:
    raw = (command.args or "").strip()
    if not raw.isdigit():
        await render(message, *V.obhod_overview(await obhod.overview()))
        return
    await _card(message, int(raw))


@router.callback_query(Adm.filter(F.s == "obhod"))
async def cb_obhod(callback: CallbackQuery, callback_data: Adm) -> None:
    a, arg = callback_data.a, callback_data.arg
    tg_s, _, extra = arg.partition(".")
    if a == "open" or not tg_s.isdigit():
        await render(callback, *V.obhod_overview(await obhod.overview()))
        return
    tg = int(tg_s)
    if a == "pkg":
        if extra not in OBHOD_PACKAGE_CATALOG:
            await callback.answer("Неизвестный пакет", show_alert=True)
            return
        ok = await obhod.apply_package(tg, extra, admin_id=callback.from_user.id)
        await callback.answer(T.DONE if ok else "Не применился: нет активного обхода или идет другая операция",
                              show_alert=not ok)
    elif a == "base":
        ok = await obhod.reset_base(tg)
        await callback.answer(T.DONE if ok else "Не получилось", show_alert=not ok)
    elif a == "off":
        await render(callback, *V.confirm(f"Выключить обход у <code>{tg}</code>?",
                                          Adm(s="obhod", a="offok", arg=str(tg)), Adm(s="obhod", a="show", arg=str(tg))))
        return
    elif a == "offok":
        ok = await obhod.deactivate(tg)
        await callback.answer(T.DONE if ok else "Обход и так не активен", show_alert=not ok)
    await _card(callback, tg, answered=a in ("pkg", "base", "offok"))
