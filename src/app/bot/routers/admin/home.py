"""Admin: panel home and stats (Adm s=panel|stats, /admin, /stats). Owner: E.

Old buttons land here through aliases: admin_panel, admin_back, admin_stats
(Adm) and ui:admin_panel:* (Nav s=admin_panel). Admins only (AdminGuard).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import Adm, Nav
from app.bot.middlewares.admin_guard import guard_router
from app.bot.views import admin as V
from app.bot.views import render
from app.services.admin_stats import bot_stats

router = guard_router(Router(name="r3_admin_home"))


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    await render(message, *V.home(await bot_stats()))


@router.callback_query(Adm.filter(F.s == "panel"))
async def cb_panel(callback: CallbackQuery) -> None:
    await render(callback, *V.home(await bot_stats()))


@router.callback_query(Nav.filter(F.s == "admin_panel"))
async def cb_legacy_panel(callback: CallbackQuery) -> None:
    await render(callback, *V.home(await bot_stats()))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    await render(message, *V.stats(await bot_stats()))


@router.callback_query(Adm.filter(F.s == "stats"))
async def cb_stats(callback: CallbackQuery) -> None:
    await render(callback, *V.stats(await bot_stats()))
