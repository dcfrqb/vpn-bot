"""Admin decision on a held payment (2.x entry point, release 3.0 shim).

3.0: the decision lives in app.services.fulfillment.Fulfillment.decide_review
(the new admin router app.bot.routers.admin.payments calls it; old ``rv_ok:`` /
``rv_no:`` buttons reach that router through legacy_aliases). This function
keeps the 2.x signature for routers/admin.py until the cutover.
"""
from typing import Tuple

from app.domain.texts.checkout import REVIEW_TEXT

APPROVED = "approved"
PENDING = "pending"
REJECTED = "rejected"
ALREADY_DONE = "already_done"
ALREADY_REJECTED = "already_rejected"
ALREADY_APPROVED = "already_approved"
NOT_HELD = "not_held"
NOT_PAID = "not_paid"
NOT_FOUND = "not_found"
BUSY = "busy"

RESULT_TEXT = REVIEW_TEXT


async def decide_held_payment(payment_row_id: int, admin_id: int, approve: bool, bot) -> Tuple[str, str]:
    """Returns (result code, text for the admin)."""
    from app.services.money import money

    code = await money().fulfillment.decide_review(int(payment_row_id), int(admin_id), approve=bool(approve))
    return code, RESULT_TEXT.get(code, code)
