"""Admin: blocklist, stop-list, referral stats/payouts, payment-request log,
legacy alias hits. Owner: E. Admins only (AdminGuard).

Commands: /block, /unblock, /stoplist, /stoplist_add, /stoplist_del,
/referral_stats [code], /referral_payout sun718 N [note], /payments_new,
/payment_find <req_id>, /legacy_hits. Screens: Adm(s=block|ref).
"""
from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import Adm
from app.bot.middlewares.admin_guard import guard_router, is_admin_id
from app.bot.views import admin as V
from app.bot.views import kb, render
from app.domain.texts import admin as T
from app.domain.texts import h
from app.logger import logger
from app.services.admin_stats import payment_requests
from app.services.blocklist import CARD_FP_RE, BlocklistAdmin
from app.services.referral import ReferralService

router = guard_router(Router(name="r3_admin_ops"))
blocklist = BlocklistAdmin()


def _id_arg(command: CommandObject) -> Any:
    raw = (command.args or "").strip().split()
    return int(raw[0]) if raw and raw[0].isdigit() else None


# ----------------------------------------------------------------- bot blocklist


@router.message(Command("block"))
async def cmd_block(message: Message, command: CommandObject) -> None:
    tg = _id_arg(command)
    if tg is None:
        await render(message, T.USAGE_ID.format(cmd="block"))
        return
    if is_admin_id(tg):
        await render(message, "Нельзя заблокировать администратора.")
        return
    await blocklist.bot_block(tg)
    await render(message, f"✅ Пользователь <code>{tg}</code> заблокирован в боте.")


@router.message(Command("unblock"))
async def cmd_unblock(message: Message, command: CommandObject) -> None:
    tg = _id_arg(command)
    if tg is None:
        await render(message, T.USAGE_ID.format(cmd="unblock"))
        return
    await blocklist.bot_unblock(tg)
    await render(message, f"✅ Пользователь <code>{tg}</code> разблокирован.")


# ----------------------------------------------------------------- stop-list (no sales)


async def _stoplist_view(event: Any) -> None:
    try:
        users, cards = await blocklist.stop_list()
    except RuntimeError:
        await render(event, T.NO_DB)
        return
    await render(event, *V.blocklist(users, cards))


@router.message(Command("stoplist"))
async def cmd_stoplist(message: Message) -> None:
    await _stoplist_view(message)


@router.callback_query(Adm.filter(F.s == "block"))
async def cb_stoplist(callback: CallbackQuery) -> None:
    await _stoplist_view(callback)


@router.message(Command("stoplist_add"))
async def cmd_stoplist_add(message: Message, command: CommandObject) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if not parts:
        await render(message, T.USAGE_STOPLIST)
        return
    key, reason = parts[0], (parts[1] if len(parts) > 1 else "")
    if key.isdigit():
        if is_admin_id(int(key)):
            await render(message, "Нельзя добавить администратора.")
            return
        await blocklist.stop_user(int(key), reason)
    elif CARD_FP_RE.match(key):
        await blocklist.stop_card(key, reason)
    else:
        await render(message, T.USAGE_STOPLIST)
        return
    await render(message, f"⛔ <code>{h(key)}</code> в стоп-листе.")


@router.message(Command("stoplist_del"))
async def cmd_stoplist_del(message: Message, command: CommandObject) -> None:
    key = (command.args or "").strip().split(maxsplit=1)[0] if command.args else ""
    if not key:
        await render(message, T.USAGE_STOPLIST)
        return
    removed = await (blocklist.unstop_user(int(key)) if key.isdigit() else blocklist.unstop_card(key))
    await render(message, f"✅ <code>{h(key)}</code> убран из стоп-листа." if removed else "Такой записи нет.")


# ----------------------------------------------------------------- referral


async def _referral(event: Any, container: Any, code: str) -> None:
    try:
        st = await ReferralService(notifier=container.notifier, settings=container.settings).stats(code)
    except RuntimeError:
        await render(event, T.NO_DB)
        return
    await render(event, V.referral(st), kb([V.BACK_TO_PANEL]))


@router.message(Command("referral_stats"))
async def cmd_referral_stats(message: Message, command: CommandObject, container: Any) -> None:
    code = ((command.args or "").strip().split() or ["sun718"])[0].lower()
    await _referral(message, container, code)


@router.callback_query(Adm.filter(F.s == "ref"))
async def cb_referral(callback: CallbackQuery, container: Any) -> None:
    await _referral(callback, container, "sun718")


@router.message(Command("referral_payout"))
async def cmd_referral_payout(message: Message, command: CommandObject, container: Any) -> None:
    parts = (command.args or "").split(maxsplit=2)
    if len(parts) < 2 or parts[0].lower() != "sun718" or not parts[1].isdigit() or int(parts[1]) <= 0:
        await render(message, T.USAGE_PAYOUT)
        return
    months, note = int(parts[1]), (parts[2] if len(parts) > 2 else "")
    try:
        before, after = await ReferralService(notifier=container.notifier, settings=container.settings).record_payout(
            message.from_user.id, months, note)
    except RuntimeError:
        await render(message, T.NO_DB)
        return
    warn = (f"⚠️ Выплачено {months} мес, а доступно было только {before}. Запись создана.\n\n"
            if months > before else "")
    await render(message, f"{warn}✅ Записано: <b>{months} мес</b> выплачено.\nДоступно теперь: <b>{after}</b>")


# ----------------------------------------------------------------- payment-request log (2.x)


@router.message(Command("payments_new"))
async def cmd_payments_new(message: Message) -> None:
    recs = payment_requests(limit=10)
    if not recs:
        await render(message, "📋 Новых заявок нет")
        return
    lines = ["📋 <b>Новые заявки на оплату</b>", ""]
    for i, rec in enumerate(recs, 1):
        payload = rec.get("payload") or {}
        lines.append(f"{i}. req_id={h(rec.get('req_id'))} tg_id={h(rec.get('tg_id'))} {h(payload.get('amount'))} RUB")
    await render(message, "\n".join(lines))


@router.message(Command("payment_find"))
async def cmd_payment_find(message: Message, command: CommandObject) -> None:
    req_id = (command.args or "").strip()
    if not req_id:
        await render(message, "Использование: <code>/payment_find PRQ-XXXXX</code>")
        return
    recs = payment_requests(req_id=req_id)
    if not recs:
        await render(message, f"Заявка {h(req_id)} не найдена")
        return
    lines = [f"📋 <b>Заявка {h(req_id)}</b>", ""]
    for rec in recs:
        lines.append(f"event={h(rec.get('event'))} ts={h(rec.get('ts'))}\n"
                     f"tg_id={h(rec.get('tg_id'))} payload={h(rec.get('payload'))}\n")
    await render(message, "\n".join(lines))


@router.message(Command("legacy_hits"))
async def cmd_legacy_hits(message: Message) -> None:
    from app.bot.legacy_aliases import alias_hit_counts

    try:
        hits = await alias_hit_counts()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"legacy_hits failed ({type(e).__name__})")
        hits = {}
    rows = sorted(((k, v) for k, v in hits.items() if v), key=lambda kv: -kv[1])
    body = "\n".join(f"• {h(k)}: {v}" for k, v in rows) or "Нажатий старых кнопок нет."
    await render(message, f"🧓 <b>Старые кнопки (2.x)</b>\n\n{body}")
