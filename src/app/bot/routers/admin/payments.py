"""Admin: payment review (AdmReview), 24h refund decisions (AdmRefund).

Owner stream: A (Money). Old 2.x review buttons ``rv_ok:<id>`` / ``rv_no:<id>``
arrive here through app.bot.legacy_aliases. Only ADMINS may decide; the
decision itself is idempotent in the services (compare-and-set), so two
admins pressing at once get one result and one "already decided".
"""
from __future__ import annotations

from typing import Any

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.callbacks import AdmRefund, AdmReview
from app.domain.texts import checkout as T
from app.domain.texts import h
from app.logger import logger
from app.services.money import money

router = Router(name="r3_admin_payments")


def _is_admin(container: Any, user_id: int) -> bool:
    return int(user_id) in set(getattr(container.settings, "ADMINS", None) or [])


async def _close(cb: CallbackQuery, result_text: str) -> None:
    """Append the decision to the admin message and drop its buttons.

    O5: the original alert text is HTML (bold labels, <code> ids). ``msg.text``
    is the plain rendering (entities stripped), so escaping it with ``h()``
    used to turn the whole message to plain text. ``msg.html_text`` renders
    the entities back to HTML markup, which needs no further escaping; the
    edit itself must ask for ``parse_mode="HTML"`` explicitly, since editing
    does not inherit the parse_mode the message was first sent with.
    """
    msg = cb.message
    if msg is None or not getattr(msg, "text", None):
        if msg is not None:
            await msg.answer(result_text)
        return
    try:
        body = msg.html_text or h(msg.text)
        await msg.edit_text(f"{body}\n\n<b>{result_text}</b>", reply_markup=None, parse_mode="HTML")
    except Exception:  # noqa: BLE001 - too old to edit: say it in a new message
        await msg.answer(result_text)


@router.callback_query(AdmReview.filter())
async def on_review(cb: CallbackQuery, callback_data: AdmReview, container: Any) -> None:
    if not _is_admin(container, cb.from_user.id):
        await cb.answer(T.NOT_ADMIN, show_alert=True)
        return
    await cb.answer("⏳")
    code = await money(container).fulfillment.decide_review(callback_data.pid, cb.from_user.id,
                                                             approve=callback_data.a == "ok")
    logger.info(f"review decision: payment={callback_data.pid} admin={cb.from_user.id} result={code}")
    await _close(cb, T.REVIEW_TEXT.get(code, code))


@router.callback_query(AdmRefund.filter())
async def on_refund_decision(cb: CallbackQuery, callback_data: AdmRefund, container: Any) -> None:
    if not _is_admin(container, cb.from_user.id):
        await cb.answer(T.NOT_ADMIN, show_alert=True)
        return
    await cb.answer("⏳")
    code = await money(container).refunds.decide(callback_data.rid, cb.from_user.id,
                                                 approve=callback_data.a == "ok")
    logger.info(f"refund decision: request={callback_data.rid} admin={cb.from_user.id} result={code}")
    if code == "done":
        text = T.ADMIN_REFUND_DONE
    elif code == "done_no_revoke":
        text = T.ADMIN_REFUND_DONE_NO_REVOKE
    elif code == "rejected":
        text = T.ADMIN_REFUND_REJECTED
    elif code == "not_found":
        text = T.ADMIN_REFUND_NOT_FOUND
    elif code.startswith("failed:"):
        await cb.message.answer(T.admin_refund_failed(code.partition(":")[2]))
        return  # keep the buttons: the admin may press «Вернуть» again
    elif code.startswith("already:"):
        text = T.admin_refund_already(code.partition(":")[2])
    else:
        text = code
    await _close(cb, text)
