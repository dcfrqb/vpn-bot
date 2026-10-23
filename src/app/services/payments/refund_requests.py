"""24-hour refund requests «Не смог подключиться» (release 3.0, stream A).

Flow (REFUND_24H_ENABLED):
  user taps «Не смог подключиться» under the payment message
    -> refund_requests row (one per payment; the DB also keeps one pending)
    -> admins get «Вернуть / Отклонить» (REFUNDS topic, DM fallback);
  «Вернуть»: pending|failed -> approved (compare-and-set = the lock), money
    back (YooKassa refund with Idempotence-Key rr:<id>, or refundStarPayment).
    Confirmed money arms a durable access-only queue; rollback removes the
    exact paid contribution under its existing lock. Pending money is read
    again, unknown Stars money outcomes require manual review, never another
    money call. SQL leases/CAS survive restart; notice delivery has its own
    retryable outbox. Only a delivered payment is eligible; the
    ``refund_24h`` marker is written before the money goes back, and
    fulfillment/recovery never grant a payment that carries it.
    An unknown money outcome remains approved, preventing a second approval.
  «Отклонить»: pending -> rejected, user told.
The YooKassa refund.succeeded webhook sees ``refund_24h`` in the payment meta
records confirmed money and rearms only money-unconfirmed access cases.
It never invokes access/store recovery while holding its payment row lock.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
import uuid
from typing import Any, Optional

from app.domain.models import AdminTopic
from app.domain.texts import checkout as T
from app.logger import logger
from app.services.payments.store import M_NEEDS_PROVISIONING, M_NEEDS_REVIEW, PaymentRecord, _naive, refund_access_due

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
        # Only a delivered payment (review money M-2): a held or not-yet-granted
        # one goes to the admin as a normal support case, not a 24h refund.
        if not rec.fulfilled or rec.meta.get(M_NEEDS_REVIEW) or rec.meta.get(M_NEEDS_PROVISIONING):
            return False
        if rec.method == "stars" and not rec.telegram_charge_id:
            return False
        paid = rec.paid_at or rec.created_at
        now = self.d.clock().replace(tzinfo=None)
        return paid is not None and now - paid <= REFUND_WINDOW

    async def request(self, telegram_id: int, payment_id: int, *, message_id: Optional[int] = None) -> str:
        """-> requested | already | not_eligible"""
        if not self.enabled():
            return "not_eligible"
        store = self.d.store
        rec = await store.get(int(payment_id))
        if not self.eligible(rec, telegram_id):
            return "not_eligible"
        if type(message_id) is int and message_id > 0:
            await store.patch_meta(rec.id, {"refund_message_id": message_id})
        rr, created = await store.create_refund_request(rec.id, rec.telegram_id, amount=rec.amount,
                                                       reason=REASON_NOT_CONNECTED)
        if not created:
            return "already"
        first, last, username = await store.user_names(rec.telegram_id)
        from app.services.payments.notices import plan_label

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
            rec = await store.get(rr.payment_id)
            await self.d.notifier.notify_user(rr.telegram_id, T.refund_rejected(support_contact(self.d.settings)),
                                              html=True, reply_markup=self.d.ui.support(),
                                              **self._card_options(rec))
            logger.info(f"refund request #{request_id} rejected by {admin_id}")
            return "rejected"

        rr = await store.transition_refund_request(request_id, ("pending", "failed"), "approved", decided_by=admin_id)
        if rr is None:
            return await self._already(request_id)
        rec = await store.get(rr.payment_id)
        if rec is None or rec.status != "succeeded":
            await store.transition_refund_request(request_id, ("approved",), "failed")
            return "failed:платеж не в статусе succeeded"
        if not rec.fulfilled:
            await store.transition_refund_request(request_id, ("approved",), "failed")
            return "failed:доступ по платежу еще не выдан, решите вручную"
        now = _naive(self.d.clock())
        # Persist the queue before money issuance. A crash after the provider
        # accepted the request is recovered by READS, never another refund().
        await store.update_refund_marker(rec.id, {"rid": rr.id, "state": "refunding", "money_status": "submitting"},
            access_patch={"state": "awaiting_money", "due_at": (now + timedelta(minutes=5)).isoformat()})
        response, _detail = await self._money_back(rec, rr.id)
        unknown = response is None
        response = response or {"status": "unknown"}
        status = response["status"]
        await store.update_refund_marker(rec.id, {"money_status": status,
            "provider_refund_id": response.get("id"), "state": "done" if status == "succeeded" else "pending_money"},
            access_patch={"state": "awaiting_money" if status == "pending" else "pending",
                          "due_at": (now + timedelta(minutes=5) if status == "pending" else now).isoformat()})
        try:
            await store.set_autorenew(rec.telegram_id, False)
        except Exception:  # noqa: BLE001 - independently retried with access
            pass
        fresh = await store.get(rec.id)
        access_state = (fresh.meta.get("refund_24h", {}).get("access") or {}).get("state")
        if access_state in ("applied", "manual_review"):
            return "done" if access_state == "applied" else ("unknown_money" if unknown else "done_no_revoke")
        if status == "pending" and fresh.meta.get("refund_24h", {}).get("money_status") != "succeeded":
            await self._notify(fresh, "pending_money")
            return "pending_money"
        if status == "succeeded":
            if rec.method == "stars":
                await store.set_status(rec.id, ("succeeded",), "refunded")
            await store.transition_refund_request(request_id, ("approved",), "refunded")
        state = await self.recover_one_access(rec.id)
        if state == "busy":
            latest = await store.get(rec.id)
            state = (latest.meta.get("refund_24h", {}).get("access") or {}).get("state")
        if unknown and state != "applied":
            return "unknown_money"
        return {"applied": "done", "manual_review": "done_no_revoke"}.get(state, "retry_access")

    async def _money_back(self, rec: PaymentRecord, request_id: int) -> tuple[Optional[dict], str]:
        try:
            if rec.method == "stars":
                if not rec.telegram_charge_id:
                    return None, "нет telegram_charge_id"
                ok = await self.d.stars.refund(rec.telegram_id, rec.telegram_charge_id)
                return ({"status": "succeeded"} if ok else None), "Telegram не подтвердил возврат звезд"
            res = await self.d.payments.refund(rec.external_id, amount_rub=None, idempotence_key=f"rr:{request_id}",
                                             reason="Возврат в течение 24 часов: не смог подключиться")
            if (not res or res.get("status") not in ("succeeded", "pending")
                    or res.get("payment_id") != rec.external_id
                    or Decimal(str(res.get("amount") or 0)) < rec.amount
                    or (res.get("currency") is not None and res["currency"] != rec.currency)):
                return None, "ЮKassa не подтвердила полный возврат"
            return res, ""
        except Exception as exc:  # noqa: BLE001 - durable queue verifies a possibly lost response
            logger.error(f"refund request #{request_id}: money outcome unknown ({type(exc).__name__})")
            return None, "платежная система не подтвердила результат, требуется проверка"

    async def recover_access(self, *, limit: int = 20) -> dict:
        """Durable access-only recovery, independently of the eligibility flag."""
        records = await self.d.store.refund_access_candidates(self.d.clock(), limit=limit)
        stats = {"checked": 0, "applied": 0, "retry": 0, "awaiting_money": 0, "manual_review": 0, "busy": 0, "notified": 0}
        for rec in records:
            stats["checked"] += 1
            try:
                if refund_access_due(rec.meta, self.d.clock()):
                    state = await self.recover_one_access(rec.id)
                else:
                    state = "notified" if await self.recover_one_notice(rec.id) else "retry"
            except Exception as exc:  # DB failure: the lease remains reclaimable
                logger.error(f"refund access recovery payment={rec.id}: {type(exc).__name__}")
                state = "retry"
            stats[state if state in stats else "retry"] += 1
        return stats

    async def _money_confirmed(self, rec):
        marker = rec.meta.get("refund_24h") or {}
        status = marker.get("money_status")
        if status == "succeeded":
            return "succeeded"
        if rec.method == "stars":
            # Old done markers alone do not prove Telegram returned money.
            return "succeeded" if rec.status == "refunded" else "manual_review"
        rid = marker.get("provider_refund_id")
        if rid:
            getter = getattr(self.d.payments, "get_refund", None)
            if getter is None:
                return "manual_review"
            evidence = await getter(str(rid))
            if not evidence:
                return "retry"
            if evidence.get("error"):
                return "manual_review"
            if (evidence.get("payment_id") != rec.external_id
                    or (evidence.get("currency") is not None and evidence["currency"] != rec.currency)):
                return "manual_review"
            if evidence.get("status") == "pending":
                return "awaiting_money"
            if evidence.get("status") != "succeeded":
                return "manual_review"
            amount = Decimal(str(evidence.get("amount") or 0))
            return "succeeded" if amount >= rec.amount > 0 else "manual_review"
        # Legacy done/revoked=False, or a crash between the provider response
        # and saving its id: verify the total refund without issuing money.
        evidence = await self.d.payments.get_payment(rec.external_id)
        if not evidence:
            return "retry"
        if evidence.get("error"):
            return "manual_review"
        if (evidence.get("id") != rec.external_id or evidence.get("currency") != rec.currency
                or Decimal(str(evidence.get("amount") or 0)) != rec.amount):
            return "manual_review"
        if Decimal(str(evidence.get("refunded_amount") or 0)) >= rec.amount > 0:
            return "succeeded"
        return "manual_review"

    async def recover_one_access(self, payment_id: int) -> str:
        owner = uuid.uuid4().hex
        rec = await self.d.store.claim_refund_access(payment_id, owner=owner, now=self.d.clock(),
                                                    lease_for=timedelta(minutes=10))
        if rec is None:
            return "busy"
        marker = rec.meta.get("refund_24h") or {}
        request_id = marker.get("rid")
        state, action, error = "manual_review", None, "invalid refund evidence"
        reason_class = "missing_evidence"
        try:
            if (type(request_id) is not int or request_id <= 0 or type(rec.period_months) is not int
                    or rec.period_months <= 0 or rec.kind != "subscription" or not rec.fulfilled
                    or rec.method not in ("stars", "yookassa")):
                error = "missing request/paid period/delivery evidence"
            else:
                rr = await self.d.store.get_refund_request(request_id)
                if (rr is None or rr.payment_id != rec.id or rr.telegram_id != rec.telegram_id
                        or rr.status not in ("approved", "refunded", "failed")):
                    error = "refund request does not match payment"
                else:
                    state = await self._money_confirmed(rec)
                    if state == "succeeded":
                        await self.d.store.update_refund_marker(rec.id, {"money_status": "succeeded", "state": "done"})
                        await self.d.store.transition_refund_request(request_id, ("approved", "failed"), "refunded")
                        if rec.method == "stars":
                            await self.d.store.set_status(rec.id, ("succeeded",), "refunded")
                        await self.d.store.set_autorenew(rec.telegram_id, False)
                        # Never use months=None here: missing evidence is a
                        # manual case, not permission for a full access cut.
                        result = await self.d.provisioning.rollback(rec.telegram_id, months=rec.period_months,
                            payment_id=rec.id, reason=f"refund_24h:{request_id}", trace_id=f"rr:{request_id}")
                        action = result.action
                        state = "applied" if result.changed else "manual_review"
                        error = "" if result.changed else f"rollback {result.action}"
                        reason_class = None if result.changed else "access_policy"
                    elif state == "manual_review":
                        error = "money refund unconfirmed/canceled or ambiguous legacy evidence"
                        reason_class = "money_unconfirmed"
        except (InvalidOperation, ValueError, TypeError) as exc:
            state, error = "manual_review", f"invalid refund evidence: {type(exc).__name__}"
        except Exception as exc:  # Includes unconfirmed obhod synchronization.
            state, error = "retry", type(exc).__name__
        finished = await self.d.store.finish_refund_access(rec.id, owner=owner, state=state,
            now=self.d.clock(), error=error, action=action, reason_class=reason_class)
        if finished is None:
            return "busy"  # expired owner cannot publish stale completion/notices
        try:
            await self.d.hooks.invalidate_caches(rec.telegram_id)
        except Exception as exc:
            logger.warning(f"refund caches payment={rec.id}: {type(exc).__name__}")
        await self._notify(finished, state)
        final_state = (finished.meta.get("refund_24h", {}).get("access") or {}).get("state")
        return "retry" if final_state == "pending" else final_state

    async def _notify(self, rec, phase):
        try:
            await self.recover_one_notice(rec.id)
        except Exception as exc:
            logger.warning(f"refund notice payment={rec.id}: {type(exc).__name__}")

    async def recover_one_notice(self, payment_id):
        """Notice-only outbox: completion is persisted AFTER confirmed delivery.

        Bound cards are edited idempotently after process failure. Legacy
        payments without a card keep notifier dedup for their send fallback.
        This can never repeat money issuance or access subtraction.
        """
        owner = uuid.uuid4().hex
        rec = await self.d.store.claim_refund_notice(payment_id, owner=owner, now=self.d.clock(),
                                                    lease_for=timedelta(minutes=5))
        if rec is None:
            return False
        marker = rec.meta.get("refund_24h") or {}
        access = marker.get("access") or {}
        phase, rid = access.get("notice_phase"), marker.get("rid")
        user_ok, admin_ok = access.get("notice_user_delivered", False), access.get("notice_admin_delivered", False)
        try:
            if not admin_ok:
                count = await self.d.notifier.notify_admins(AdminTopic.REFUNDS,
                    f"Возврат #{rid}: {'требуется ручная проверка' if phase == 'manual_review' else 'повторим корректировку доступа автоматически'}. Платеж {rec.id}. "
                    f"Причина: {access.get('last_error', '')}",
                    dedup_key=f"rr_alert:{rid}:{phase}", dedup_ttl=300)
                admin_ok = bool(count)
            if not user_ok:
                options = self._card_options(rec)
                if not options:
                    options = {"dedup_key": f"rr_notice:{rid}:{phase}", "dedup_ttl": 300}
                user_ok = await self.d.notifier.notify_user(rec.telegram_id, self._notice_text(rec, phase),
                    html=True, reply_markup=self.d.ui.support(), **options)
        except Exception as exc:
            logger.warning(f"refund notice payment={rec.id}: {type(exc).__name__}")
        await self.d.store.finish_refund_notice(rec.id, owner=owner, phase=phase, user_delivered=bool(user_ok),
                                               admin_delivered=bool(admin_ok), now=self.d.clock())
        return bool(user_ok and admin_ok)

    @staticmethod
    def _card_options(rec):
        if rec is not None:
            for key in ("refund_message_id", "checkout_message_id"):
                value = rec.meta.get(key)
                if type(value) is int and value > 0:
                    return {"edit_message_id": value}
        return {}

    def _notice_text(self, rec, phase):
        marker = rec.meta.get("refund_24h") or {}
        if phase == "applied":
            return T.REFUND_APPROVED_STARS if rec.method == "stars" else T.REFUND_APPROVED_CARD
        if phase == "pending_money":
            return "Проверяем подтверждение возврата денег. Срок подписки пока не менялся."
        if phase == "retry":
            return "Возврат денег подтвержден. Корректировку срока доступа повторим автоматически."
        if marker.get("money_status") == "succeeded" or (rec.method == "stars" and rec.status == "refunded"):
            return T.REFUND_APPROVED_STARS_REVIEW if rec.method == "stars" else T.REFUND_APPROVED_CARD_REVIEW
        return "Результат возврата денег проверит поддержка. Срок подписки автоматически не менялся."

    async def _already(self, request_id: int) -> str:
        rr = await self.d.store.get_refund_request(request_id)
        return f"already:{rr.status}" if rr else "not_found"
