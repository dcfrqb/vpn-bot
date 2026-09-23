"""Admin: users and payments lists, /whois, /sync, /syncme. Owner: E.

Aliases landing here: admin_users[_page_N], admin_payments[_filter|_page_N_x]
(Adm s=users|payments) and ui:admin_users:* / ui:admin_payments:* (Nav).
Admins only (AdminGuard).
"""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import Adm, Nav
from app.bot.middlewares.admin_guard import guard_router, is_admin_id
from app.bot.views import admin as V
from app.bot.views import render
from app.domain.texts import admin as T
from app.domain.texts import fmt_date_msk, h
from app.logger import logger
from app.services.admin_stats import user_card
from app.services.blocklist import BlocklistAdmin
from app.services.stats import get_payments_list, get_users_list

router = guard_router(Router(name="r3_admin_users"))
PAGE_SIZE = 10


def _int(value: Any, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


async def _users(event: Any, page: int) -> None:
    await render(event, *V.users(await get_users_list(page=page, page_size=PAGE_SIZE)))


async def _payments(event: Any, page: int, flt: str) -> None:
    flt = flt if flt in V.PAYMENT_FILTERS else "all"
    data = await get_payments_list(page=page, page_size=PAGE_SIZE, status=None if flt == "all" else flt)
    await render(event, *V.payments(data, flt))


@router.callback_query(Adm.filter(F.s == "users"))
async def cb_users(callback: CallbackQuery, callback_data: Adm) -> None:
    await _users(callback, _int(callback_data.arg) if callback_data.a == "page" else 1)


@router.callback_query(Adm.filter(F.s == "payments"))
async def cb_payments(callback: CallbackQuery, callback_data: Adm) -> None:
    a, arg = callback_data.a, callback_data.arg
    if a == "page":
        page, _, flt = arg.partition(".")
        await _payments(callback, _int(page), flt or "all")
    elif a == "filter":
        await _payments(callback, 1, arg)
    else:
        await _payments(callback, 1, "all")


@router.callback_query(Nav.filter(F.s.in_({"admin_users", "admin_payments"})))
async def cb_legacy_lists(callback: CallbackQuery, callback_data: Nav) -> None:
    # p = "<action>[.<payload>]"; payload "3" (users) or "2&all" (payments)
    _, _, payload = callback_data.p.partition(".")
    page, _, flt = payload.partition("&")
    if callback_data.s == "admin_users":
        await _users(callback, _int(page))
    else:
        await _payments(callback, _int(page), flt or "all")


def _target(command: CommandObject) -> Any:
    raw = (command.args or "").strip().split()
    if not raw or not raw[0].lstrip("-").isdigit():
        return None
    return int(raw[0])


@router.message(Command("whois"))
async def cmd_whois(message: Message, command: CommandObject, container: Any) -> None:
    tg = _target(command)
    if tg is None:
        await render(message, T.USAGE_ID.format(cmd="whois"))
        return
    try:
        state = await container.status.get_state(tg, force=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"whois: status failed tg={tg} ({type(e).__name__})")
        state = None
    card = await user_card(tg)
    await render(message, V.whois(card, state, bot_blocked=BlocklistAdmin.bot_blocked(tg), is_admin=is_admin_id(tg)))


async def _sync(message: Message, tg: int, container: Any) -> None:
    await container.status.invalidate(tg)
    try:
        st = await container.status.get_state(tg, force=True)
    except Exception as e:  # noqa: BLE001
        await render(message, f"❌ Синхронизация {tg} не удалась ({h(type(e).__name__)})")
        return
    status = "панель недоступна" if st.stale else ("✅ активна" if st.active else "❌ нет")
    await render(message, (f"<b>Sync {tg}</b>\nПодписка: {status}\nТариф: {h(st.plan_code or '—')}\n"
                           f"До: {fmt_date_msk(st.expires_at, with_time=True)}"))


@router.message(Command("sync"))
async def cmd_sync(message: Message, command: CommandObject, container: Any) -> None:
    tg = _target(command)
    if tg is None:
        await render(message, T.USAGE_ID.format(cmd="sync"))
        return
    await _sync(message, tg, container)


@router.message(Command("syncme"))
async def cmd_syncme(message: Message, container: Any) -> None:
    await _sync(message, message.from_user.id, container)
