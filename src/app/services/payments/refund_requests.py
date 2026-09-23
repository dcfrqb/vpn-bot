"""24-hour refund requests «Не смог подключиться» (release 3.0, stream A).

Flow (REFUND_24H_ENABLED):
  user taps «Не смог подключиться» under the payment message
    -> refund_requests row (one per payment; the DB also keeps one pending)
    -> admins get «Вернуть / Отклонить» (REFUNDS topic, DM fallback);
  «Вернуть»: pending|failed -> approved (compare-and-set = the lock), money
    back (YooKassa refund with Idempotence-Key rr:<id>, or refundStarPayment),
    access cut through ProvisioningService.revoke, autorenew off, user told;
    request -> refunded. A failed money refund -> failed, the admin may retry.
  «Отклонить»: pending -> rejected, user told.
The YooKassa refund.succeeded webhook sees ``refund_24h`` in the payment meta
and only records the money (no second rollback, no second user message).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from app.domain.models import AdminTopic, SubKind
from app.domain.texts import checkout as T
from app.logger import logger
from app.services.payments.store import PaymentRecord

REFUND_WINDOW = timedelta(hours=24)
REASON_NOT_CONNECTED = "not_connected"


def support_contact(settings: Any) -> str:
    handle = getattr(settings, "SUPPORT_HANDLE", None) or getattr(settings, "ADMIN_SUPPORT_USERNAME", None)
    if handle:
        handle = str(handle).strip()
        return handle if handle.startswith("@") else f"@{handle}"
    return "в поддержку"


class RefundRequests:
    def __init__(self, deps: Any):
        self.d = deps

    def enabled(self) -> bool:
        return bool(getattr(self.d.settings, "REFUND_24H_ENABLED", False))

    def eligible(self, rec: Optional[PaymentRecord], telegram_id: int) -> bool:
        if rec is None or rec.telegram_id != int(telegram_id):
            return False
        if rec.status != "succeeded" or rec.kind != "subscription" or rec.method not in ("yookassa", "stars"):
            return False
        if rec.method == "stars" and not rec.telegram_charge_id:
            return False
        paid = rec.paid_at or rec.created_at
        now = self.d.clock().replace(tzinfo=None)
        return paid is not None and now - paid <= REFUND_WINDOW

    async def request(self, telegram_id: int, payment_id: int) -> str:
        """-> requested | already | not_eligible"""
        if not self.enabled():
            return "not_eligible"
        store = self.d.store
        rec = await store.get(int(payment_id))
        if not self.eligible(rec, telegram_id):
            return "not_eligible"
        rr, created = await store.create_refund_request(rec.id, rec.telegram_id, amount=rec.amount,
                                                       reason=REASON_NOT_CONNECTED)
        if not created:
            return "already"
        first, last, username = await store.user_names(rec.telegram_id)
        from app.services.fulfillment import plan_label

        await self.d.notifier.notify_admins(
            AdminTopic.REFUNDS,
            T.admin_refund_request(
                request_id=rr.id, full_name=f"{first} {last}".strip(), username=username,
                telegram_id=rec.telegram_id, payment_id=rec.id, external_id=rec.external_id,
                plan_label=plan_label(rec.plan_code, rec.period_months), amount=rec.amount,
                currency=rec.currency, paid_at=rec.paid_at or rec.created_at,
            ),
            html=True, reply_markup=self.d.ui.refund_admin(rr.id),
        )
        logger.info(f"refund request #{rr.id}: payment={rec.id} tg_id={rec.telegram_id}")
        return "requested"

    async def decide(self, request_id: int, admin_id: int, approve: bool) -> str:
        """-> done | done_no_revoke | rejected | failed:<detail> | already:<status> | not_found"""
        store = self.d.store
        if not approve:
            rr = await store.transition_refund_request(request_id, ("pending",), "rejected", decided_by=admin_id)
            if rr is None:
                return await self._already(request_id)
            await self.d.notifier.notify_user(rr.telegram_id, T.refund_rejected(support_contact(self.d.settings)),
                                              html=True)
            logger.info(f"refund request #{request_id} rejected by {admin_id}")
            return "rejected"

        rr = await store.transition_refund_request(request_id, ("pending", "failed"), "approved", decided_by=admin_id)
        if rr is None:
            return await self._already(request_id)
        rec = await store.get(rr.payment_id)
        if rec is None or rec.status != "succeeded":
            await store.transition_refund_request(request_id, ("approved",), "failed")
            return "failed:платеж не в статусе succeeded"
        marker = dict(rec.meta.get("refund_24h") or {})
        marker.update({"rid": rr.id, "state": "refunding"})
        await store.patch_meta(rec.id, {"refund_24h": marker})

        ok, detail = await self._money_back(rec, rr.id)
        if not ok:
            await store.transition_refund_request(request_id, ("approved",), "failed")
            marker["state"] = "failed"
            await store.patch_meta(rec.id, {"refund_24h": marker})
            logger.error(f"refund request #{request_id}: money refund failed ({detail})")
            return f"failed:{detail}"

        if rec.method == "stars":
            await store.set_status(rec.id, ("succeeded",), "refunded")
        revoked = False
        try:
            revoked = await self.d.provisioning.revoke(rec.telegram_id, sub_kind=SubKind.MAIN,
                                                       reason=f"refund_24h:{rr.id}", trace_id=f"rr:{rr.id}")
        except Exception as e:  # noqa: BLE001 - money is back already; the admin is told
            logger.error(f"refund request #{request_id}: revoke failed {type(e).__name__}: {e}")
        try:
            await store.set_autorenew(rec.telegram_id, False)
        except Exception:  # noqa: BLE001
            pass
        marker.update({"state": "done", "revoked": bool(revoked)})
        await store.patch_meta(rec.id, {"refund_24h": marker})
        await store.transition_refund_request(request_id, ("approved",), "refunded")
        await self.d.hooks.invalidate_caches(rec.telegram_id)
        text = T.REFUND_APPROVED_STARS if rec.method == "stars" else T.REFUND_APPROVED_CARD
        await self.d.notifier.notify_user(rec.telegram_id, text, html=True)
        logger.warning(f"refund request #{request_id}: refunded payment={rec.id} revoked={revoked} by={admin_id}")
        return "done" if revoked else "done_no_revoke"

    async def _money_back(self, rec: PaymentRecord, request_id: int) -> tuple[bool, str]:
        if rec.method == "stars":
            if not rec.telegram_charge_id:
                return False, "нет telegram_charge_id"
            ok = await self.d.stars.refund(rec.telegram_id, rec.telegram_charge_id)
            return bool(ok), "" if ok else "Telegram отказал в возврате звезд"
        res = await self.d.payments.refund(rec.external_id, amount_rub=None, idempotence_key=f"rr:{request_id}",
                                           reason="Возврат в течение 24 часов: не смог подключиться")
        if not res or res.get("status") not in ("succeeded", "pending"):
            return False, "ЮKassa не приняла возврат"
        return True, ""

    async def _already(self, request_id: int) -> str:
        rr = await self.d.store.get_refund_request(request_id)
        return f"already:{rr.status}" if rr else "not_found"
