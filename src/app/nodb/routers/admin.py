"""
No-DB админские команды — по логам JSONL.
/payments_new — последние 10 заявок
/payment_find <req_id> — поиск по req_id

Обработчики /friend (friend_grant_*, friend_reject_*) — в app.routers.admin
"""
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.config import is_admin
from app.nodb.logs import read_last_payment_requests, find_payment_by_req_id
from app.utils.html import escape_html


router = Router(name="nodb_admin")


@router.message(Command("payments_new"))
async def cmd_payments_new(message: Message):
    """Последние 10 заявок из logs/payments.jsonl"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет прав")
        return

    lines = read_last_payment_requests(limit=10)
    if not lines:
        await message.answer("📋 Новых заявок нет")
        return

    text = "📋 <b>Новые заявки на оплату</b>\n\n"
    for i, rec in enumerate(reversed(lines), 1):
        payload = rec.get("payload", {})
        req_id = escape_html(str(rec.get("req_id", "")))
        tg_id = escape_html(str(rec.get("tg_id", "")))
        amount = escape_html(str(payload.get("amount", "")))
        text += f"{i}. req_id={req_id} tg_id={tg_id} {amount} RUB\n"
    await message.answer(text, parse_mode="HTML")


@router.message(Command("payment_find"))
async def cmd_payment_find(message: Message):
    """Поиск заявки по req_id в payments.jsonl"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Нет прав")
        return

    parts = message.text.split(maxsplit=1)
    req_id = parts[1].strip() if len(parts) > 1 else None
    if not req_id:
        await message.answer("Использование: /payment_find PRQ-XXXXX")
        return

    found = find_payment_by_req_id(req_id)
    if not found:
        await message.answer(f"Заявка {req_id} не найдена")
        return

    text = f"📋 <b>Заявка {escape_html(req_id)}</b>\n\n"
    for rec in found:
        ev = escape_html(str(rec.get("event", "")))
        ts = escape_html(str(rec.get("ts", "")))
        tg_id = escape_html(str(rec.get("tg_id", "")))
        payload_str = escape_html(str(rec.get("payload", {})))
        text += f"event={ev} ts={ts}\ntg_id={tg_id} payload={payload_str}\n\n"
    await message.answer(text, parse_mode="HTML")
