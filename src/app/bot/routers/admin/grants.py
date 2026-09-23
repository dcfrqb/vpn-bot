"""Admin: decisions on /friend and /admin requests, access requests, /grant. Owner: E.

Callbacks: Adm(s=friend|promo_req, a=grant_1m|grant_3m|grant_forever|reject,
arg=<user id>[.<request ts>]) and Adm(s=access, ...). 2.x buttons
friend_grant_*, admin_promo_grant_*, admin_grant_*, admin_reject_* land here
through aliases (their arg is the bare user id). Admins only (AdminGuard).
"""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import Adm, Nav
from app.bot.middlewares.admin_guard import guard_router
from app.bot.views import kb, render, url_btn
from app.domain.texts import admin as T
from app.domain.texts import h
from app.domain.texts import promo as TP
from app.logger import logger
from app.services.grants import GRANT_KEYS, GrantsService

router = guard_router(Router(name="r3_admin_grants"))


def _service(container: Any) -> GrantsService:
    return GrantsService(provisioning=container.provisioning, status=container.status, notifier=container.notifier)


async def _mark_processed(callback: CallbackQuery, line: str) -> None:
    msg = callback.message
    base = (getattr(msg, "html_text", None) or getattr(msg, "text", None) or "").split("\n\n")[0]
    who = h(callback.from_user.full_name or callback.from_user.id)
    try:
        await msg.edit_text(f"{base}\n\n{T.PROCESSED}\n{line}\nРешение: {who}", reply_markup=None, parse_mode="HTML")
    except Exception as e:  # noqa: BLE001 - the decision is already taken
        logger.debug(f"grant request: edit failed ({type(e).__name__})")


@router.callback_query(Adm.filter(F.s.in_({"friend", "promo_req"})))
async def cb_request(callback: CallbackQuery, callback_data: Adm, container: Any) -> None:
    uid_s, _, req = callback_data.arg.partition(".")
    if not uid_s.isdigit():
        await callback.answer("Неверные данные", show_alert=True)
        return
    uid = int(uid_s)
    legacy = not req
    request_key = f"{callback_data.s}:{callback_data.arg}"
    svc = _service(container)
    if callback_data.a == "reject":
        if not await svc.reject(request_key, legacy=legacy):
            await callback.answer(T.ALREADY_DONE, show_alert=True)
            return
        support = (getattr(container.settings, "SUPPORT_HANDLE", None)
                   or getattr(container.settings, "ADMIN_SUPPORT_USERNAME", None) or "dcfrq")
        await container.notifier.notify_user(
            uid, TP.ACCESS_REJECTED, reply_markup=kb([[url_btn("✍️ Написать", f"https://t.me/{support.lstrip('@')}")]]))
        await callback.answer("Запрос отклонен")
        await _mark_processed(callback, "❌ Запрос отклонен.")
        return
    key = callback_data.a.removeprefix("grant_")
    if key not in GRANT_KEYS:
        await callback.answer("Неверные данные", show_alert=True)
        return
    res = await svc.grant_key(callback.from_user.id, uid, key, request_key=request_key, legacy=legacy)
    if res.status == "dup":
        await callback.answer(T.ALREADY_DONE, show_alert=True)
        return
    if res.status == "busy":
        await callback.answer(T.IN_PROGRESS, show_alert=True)
        return
    if res.status != "ok":
        await callback.answer(T.GRANT_FAILED, show_alert=True)
        return
    await container.notifier.notify_user(
        uid, TP.ACCESS_GRANTED.format(what=h(res.label)), html=True,
        reply_markup=kb([[(TP.BTN_CONNECT, Nav(s="connect"))]]))
    await callback.answer(f"✅ {res.label}: выдано")
    await _mark_processed(callback, f"⭐ {h(res.label)}: выдано администратором.")


@router.callback_query(Adm.filter(F.s == "access"))
async def cb_access(callback: CallbackQuery) -> None:
    """2.x admin_grant_*/admin_reject_*: always handled by hand in the panel."""
    await callback.answer(T.USE_PANEL, show_alert=True)


@router.message(Command("grant"))
async def cmd_grant(message: Message, command: CommandObject, container: Any) -> None:
    """/grant <telegram_id> <days> [plan]: +days to the current plan (or the given plan)."""
    parts = (command.args or "").split()
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit() or not 0 < int(parts[1]) <= 3650:
        await render(message, T.USAGE_GRANT)
        return
    tg, days = int(parts[0]), int(parts[1])
    plan = parts[2].lower() if len(parts) > 2 else None
    if plan:
        from app.domain.plans import is_valid_plan_code

        if not is_valid_plan_code(plan):
            await render(message, f"Неизвестный тариф: {h(plan)}")
            return
    res = await _service(container).grant_days(message.from_user.id, tg, days, plan=plan,
                                               request_key=f"cmd:{message.chat.id}:{message.message_id}")
    if res.status == "ok":
        from app.domain.texts import fmt_date_msk

        await render(message, f"✅ <code>{tg}</code>: {h(res.label)}, до {fmt_date_msk(res.state.expires_at)}")
        await container.notifier.notify_user(
            tg, TP.ACCESS_GRANTED.format(what=f"Подписка продлена: +{days} дн."), html=True,
            reply_markup=kb([[(TP.BTN_CONNECT, Nav(s="connect"))]]))
    elif res.status == "skipped":
        await render(message, T.NOTHING_TO_EXTEND)
    elif res.status == "busy":
        await render(message, T.IN_PROGRESS)
    elif res.status == "dup":
        await render(message, T.ALREADY_DONE)
    else:
        await render(message, T.GRANT_FAILED)
