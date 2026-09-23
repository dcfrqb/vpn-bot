"""User side of 24h refund requests (RefundReq): «Не смог подключиться».

Owner stream: A (Money). Behind REFUND_24H_ENABLED; the logic lives in
app.services.payments.refund_requests.
"""
from __future__ import annotations

from typing import Any

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.callbacks import RefundReq
from app.bot.views.money import paid_kb
from app.domain.texts import checkout as T
from app.logger import logger
from app.services.money import money

router = Router(name="r3_refund")

_TEXTS = {
    "requested": T.REFUND_REQUESTED,
    "already": T.REFUND_ALREADY,
    "not_eligible": T.REFUND_NOT_ELIGIBLE,
}


@router.callback_query(RefundReq.filter())
async def on_refund_request(cb: CallbackQuery, callback_data: RefundReq, container: Any) -> None:
    result = await money(container).refunds.request(cb.from_user.id, callback_data.pid)
    logger.info(f"refund request: tg_id={cb.from_user.id} payment={callback_data.pid} result={result}")
    await cb.answer()
    if result in ("requested", "already") and cb.message is not None:
        try:
            await cb.message.edit_reply_markup(reply_markup=paid_kb(callback_data.pid, refund_button=False))
        except Exception:  # noqa: BLE001 - old message: the text below is enough
            pass
    if cb.message is not None:
        await cb.message.answer(_TEXTS.get(result, T.REFUND_NOT_ELIGIBLE))
