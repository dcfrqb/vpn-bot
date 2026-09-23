"""Fulfillment: one state machine for every paid payment (release 3.0, A).

Every entry point ends here, so a payment is granted once no matter who sees
the money first: the YooKassa webhook, «Проверить оплату», the recovery job,
an admin approving a held payment, or Telegram's successful_payment (Stars).

    pending --provider: succeeded--> succeeded --price gate--> held (admin)
       |                                 |                      | approve
       +--provider: canceled--> canceled +----------------------+--> grant --> fulfilled --> notified
                                                                     | fails
                                                                     +--> needs_provisioning (retry later)

Rules:
- The provider is the source of truth for "paid" (webhook = trigger only);
  Stars are paid only by Telegram's successful_payment.
- The price gate runs ONCE per payment (``price_ok`` in the payment meta) and
  before anything is granted; an admin approval is the only bypass.
- A per-payment Redis lock (the 2.x ``provision_lock:<external_id>``, shared
  with the reconciler) serialises processes; the DB compare-and-set guards
  the rest. Granting is idempotent per payment (ProvisioningService contract),
  user and admin messages are claimed in the payment meta before sending.
- No exception text reaches users; admins get one alert per payment (6 h dedup).
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from app.domain.models import AdminTopic, Entitlement, EntitlementSource, SubscriptionState
from app.domain.plans import is_valid_plan_code
from app.domain.texts import checkout as T
from app.logger import logger
from app.services.payments.notices import FulfillmentNotices, plan_label  # noqa: F401 (plan_label re-export)
from app.services.payments.pricing import (
    amount_fallback_plan,
    months_to_days,
    price_mismatch_reason,
    stars_mismatch_reason,
)
from app.services.payments.store import (
    M_NEEDS_PROVISIONING,
    M_NEEDS_REVIEW,
    M_REVIEW_APPROVED,
    M_REVIEW_REJECTED,
    PENDING_STATUSES,
    PaymentRecord,
)

ALERT_TTL_S = 6 * 3600
DISABLED_REVIEW_REASON = (
    "пользователь отключен вручную в панели (DISABLED), оплата получена, реши вручную: "
    "«Одобрить и выдать» включит юзера и выдаст оплаченный срок"
)
RECOVERY_PENDING_AGE = timedelta(minutes=15)
RECOVERY_STUCK_AGE = timedelta(minutes=5)
RECOVERY_HORIZON = timedelta(days=30)
RECOVERY_LIMIT = 20


class Outcome(str, enum.Enum):
    PENDING = "pending"          # not paid yet
    CANCELED = "canceled"        # provider canceled, nothing charged
    FULFILLED = "fulfilled"      # granted now
    ALREADY = "already"          # granted before
    HELD = "held"                # waiting for an admin (price/disabled user)
    REJECTED = "rejected"        # admin rejected
    REFUNDED = "refunded"
    NOT_FOUND = "not_found"
    BUSY = "busy"                # another process holds the payment lock
    RETRY = "retry"              # paid, grant failed or provider unreachable: try later


@dataclass
class FulfilResult:
    outcome: Outcome
    payment: Optional[PaymentRecord] = None
    state: Optional[SubscriptionState] = None
    detail: str = ""


class _GiftUnavailable(Exception):
    pass


def _refusal(exc: BaseException) -> Optional[str]:
    """A grant refusal that needs an admin (nothing was written), or None.

    Stream B raises GrantRefused("disabled" | "bad_plan" | ...); the 2.x core
    raised RemnaUserDisabledError."""
    from app.services.provisioning_rules import GrantRefused
    from app.services.remna_tariff import RemnaUserDisabledError

    if isinstance(exc, RemnaUserDisabledError):
        return "disabled"
    if isinstance(exc, GrantRefused):
        return exc.reason or "refused"
    return None


class Fulfillment(FulfillmentNotices):
    def __init__(self, deps: Any):
        self.d = deps

    # ------------------------------------------------------------------ locks

    async def _lock(self, external_id: str) -> bool:
        from app.services.cache import acquire_provision_lock

        return await acquire_provision_lock(external_id)

    async def _unlock(self, external_id: str) -> None:
        from app.services.cache import release_provision_lock

        await release_provision_lock(external_id)

    # ------------------------------------------------------------------ entry points

    async def process(self, payment_id: int, *, source: str, provider_view: Optional[dict] = None,
                      trace_id: Optional[str] = None) -> FulfilResult:
        trace = trace_id or f"ff-{source}-{payment_id}-{uuid.uuid4().hex[:6]}"
        store = self.d.store
        rec = await store.get(payment_id)
        if rec is None:
            return FulfilResult(Outcome.NOT_FOUND)
        if rec.status in PENDING_STATUSES:
            if rec.provider != "yookassa":
                return FulfilResult(Outcome.PENDING, rec)
            view = provider_view or await self.d.payments.get_payment(rec.external_id)
            if view is None:
                return FulfilResult(Outcome.RETRY, rec, detail="provider unavailable")
            if view.get("error") == "not_found":
                return FulfilResult(Outcome.NOT_FOUND, rec)
            status = view.get("status")
            if status == "canceled":
                await store.set_status(rec.id, PENDING_STATUSES, "canceled",
                                       {"cancellation_reason": view.get("cancellation_reason")})
                return FulfilResult(Outcome.CANCELED, rec)
            if status != "succeeded":
                return FulfilResult(Outcome.PENDING, rec)
            rec = await store.mark_paid(
                rec.id,
                amount=Decimal(str(view.get("amount") or rec.amount)),
                card_fingerprint=view.get("card_fingerprint"),
                meta_patch={"paid_currency": view.get("currency"), "payment_method": view.get("payment_method"),
                            "paid_source": source},
            ) or rec
            logger.info(f"[{trace}] payment paid: id={rec.id} ext={rec.external_id} tg_id={rec.telegram_id}")
            await self._card_check(rec, view.get("card_fingerprint"), trace)
            payer = str((view.get("metadata") or {}).get("tg_user_id") or rec.telegram_id)
            if payer != str(rec.telegram_id):
                logger.error(f"[{trace}] payer mismatch payment={rec.id}: meta={payer} row={rec.telegram_id}")
                await self._hold(rec, "tg_user_id в YooKassa не совпадает с плательщиком в БД", trace)
                return FulfilResult(Outcome.HELD, rec)
        elif rec.status == "refunded":
            return FulfilResult(Outcome.REFUNDED, rec)
        elif rec.status in ("canceled", "failed"):
            return FulfilResult(Outcome.CANCELED, rec)
        elif rec.status != "succeeded":
            return FulfilResult(Outcome.PENDING, rec)
        return await self._fulfil_paid(rec, source=source, trace=trace)

    async def process_external(self, external_id: str, *, source: str, provider_view: Optional[dict] = None,
                               trace_id: Optional[str] = None) -> FulfilResult:
        """Webhook entry: by provider id. A payment unknown to the DB (created in
        the YooKassa dashboard, lost insert) is adopted from the provider view."""
        rec = await self.d.store.get_by_external(external_id)
        if rec is None:
            view = provider_view or await self.d.payments.get_payment(external_id)
            if view is None:
                return FulfilResult(Outcome.RETRY, detail="provider unavailable")
            if view.get("error") == "not_found":
                return FulfilResult(Outcome.NOT_FOUND)
            rec = await self._adopt(external_id, view)
            if rec is None:
                return FulfilResult(Outcome.NOT_FOUND)
            provider_view = view
        return await self.process(rec.id, source=source, provider_view=provider_view, trace_id=trace_id)

    async def on_stars_paid(self, *, telegram_id: int, payment_id: int, charge_id: str, total_amount: int,
                            currency: str) -> FulfilResult:
        """Telegram successful_payment. Idempotent per charge id."""
        store = self.d.store
        rec = await store.get(payment_id)
        if rec is None or rec.telegram_id != int(telegram_id) or rec.method != "stars":
            logger.error(f"stars payment for unknown row: payment={payment_id} tg_id={telegram_id}")
            await self.d.notifier.notify_admins(
                AdminTopic.PAYMENTS,
                f"Оплата звездами без платежа в БД: tg_id={telegram_id}, payment={payment_id}, "
                f"{total_amount} {currency}, charge={charge_id}. Проверь и верни звезды вручную.",
                dedup_key=f"stars:orphan:{charge_id}", dedup_ttl=ALERT_TTL_S,
            )
            return FulfilResult(Outcome.NOT_FOUND)
        if rec.status in PENDING_STATUSES:
            rec = await store.mark_paid(
                rec.id, amount=Decimal(int(total_amount)), charge_id=charge_id,
                meta_patch={"paid_currency": currency, "paid_source": "stars"},
            ) or rec
        elif rec.telegram_charge_id and rec.telegram_charge_id != charge_id:
            logger.error(f"stars: second charge {charge_id} for payment {rec.id}")
            await self.d.notifier.notify_admins(
                AdminTopic.PAYMENTS,
                f"Повторная оплата звездами одного счета: payment #{rec.id}, tg_id={telegram_id}, "
                f"charge={charge_id}. Верни звезды вручную.",
                dedup_key=f"stars:dup:{charge_id}", dedup_ttl=ALERT_TTL_S,
            )
            return FulfilResult(Outcome.ALREADY, rec)
        return await self.process(rec.id, source="stars")

    # ------------------------------------------------------------------ core

    async def _fulfil_paid(self, rec: PaymentRecord, *, source: str, trace: str) -> FulfilResult:
        store = self.d.store
        if not await self._lock(rec.external_id):
            logger.info(f"[{trace}] payment {rec.id} busy (another process fulfils it)")
            return FulfilResult(Outcome.BUSY, rec)
        try:
            rec = await store.get(rec.id) or rec
            if rec.fulfilled:
                await self._notify(rec, None, trace)
                return FulfilResult(Outcome.ALREADY, rec)
            meta = rec.meta
            if meta.get(M_REVIEW_REJECTED):
                return FulfilResult(Outcome.REJECTED, rec)
            approved = bool(meta.get(M_REVIEW_APPROVED))
            if meta.get(M_NEEDS_REVIEW) and not approved:
                return FulfilResult(Outcome.HELD, rec)
            if not meta.get("price_ok"):
                reason = self.price_reason(rec)
                if reason and not approved:
                    await self._hold(rec, reason, trace)
                    return FulfilResult(Outcome.HELD, rec)
                await store.patch_meta(rec.id, {"price_ok": True, "price_reason": reason})
            try:
                state, patch = await self._deliver(rec, approved=approved, trace=trace)
            except Exception as e:  # noqa: BLE001 - classified below, never shown to users
                refusal = _refusal(e)
                if refusal == "disabled" and not approved:
                    await self._hold(rec, DISABLED_REVIEW_REASON, trace)
                    return FulfilResult(Outcome.HELD, rec)
                if refusal is not None and refusal != "disabled":
                    await self._hold(rec, f"выдача отклонена ({refusal}): {str(e)[:200]}", trace)
                    return FulfilResult(Outcome.HELD, rec)
                return await self._grant_failed(rec, e, trace)
            await store.mark_fulfilled(rec.id, meta_patch=patch)
            rec = await store.get(rec.id) or rec
            logger.info(f"[{trace}] payment fulfilled: id={rec.id} kind={rec.kind} tg_id={rec.telegram_id}")
            await self._after(rec, state, trace)
            return FulfilResult(Outcome.FULFILLED, rec, state)
        finally:
            await self._unlock(rec.external_id)

    def price_reason(self, rec: PaymentRecord) -> Optional[str]:
        if rec.method == "stars" or rec.currency == "XTR":
            return stars_mismatch_reason(rec.amount, rec.expected_stars)
        if not rec.plan_code or (rec.kind != "obhod_package" and not rec.period_months):
            return "в платеже нет тарифа или срока (создан не ботом?)"
        currency = rec.meta.get("paid_currency") or rec.currency
        return price_mismatch_reason(rec.plan_code, rec.period_months, float(rec.amount), currency, rec.meta)

    async def _deliver(self, rec: PaymentRecord, *, approved: bool, trace: str) -> tuple[Optional[SubscriptionState], dict]:
        plan, months = rec.plan_code, rec.period_months
        if (not plan or not months) and rec.kind != "obhod_package":
            if not approved:
                raise ValueError("payment without plan reached delivery unapproved")
            plan, months = amount_fallback_plan(float(rec.amount))
            logger.error(f"[{trace}] AMOUNT FALLBACK after admin approval: payment={rec.id} -> {plan}/{months}")
        if rec.kind == "obhod_package":
            ok = await self.d.hooks.apply_obhod_package(rec.telegram_id, plan, rec.id, trace)
            return None, {"obhod_package_applied": bool(ok)}
        if rec.kind == "gift":
            create_gift = getattr(self.d.promo, "create_gift", None)
            if create_gift is None:
                raise _GiftUnavailable("PromoService.create_gift is not available yet (stream E)")
            code = await create_gift(rec.telegram_id, plan, int(months), payment_id=rec.id)
            if not code:
                raise _GiftUnavailable("create_gift returned no code")
            return None, {"gift_code": str(code)}
        if not is_valid_plan_code(plan):
            raise ValueError(f"unknown plan {plan!r}")
        source = EntitlementSource.AUTORENEW if rec.kind == "autorenew" else EntitlementSource.PAYMENT
        ent = Entitlement(
            plan_code=plan, source=source, days=months_to_days(int(months), self.d.clock()),
            payment_id=rec.id, note=f"months={int(months)}",
        )
        # Stream B extension: calendar months from max(now, current expiry) (days
        # stay as a fallback for a provider without it); an admin-approved payment
        # may enable a user an admin disabled. Idempotent per payment id (pay:<id>).
        kwargs: dict[str, Any] = {"months": int(months)}
        if approved:
            kwargs["enable_if_disabled"] = True
        state = await self.d.provisioning.grant(rec.telegram_id, ent, trace_id=f"pay:{rec.id}", **kwargs)
        return state, {}

    async def _grant_failed(self, rec: PaymentRecord, exc: BaseException, trace: str) -> FulfilResult:
        err = f"{type(exc).__name__}: {str(exc)[:300]}"
        logger.error(f"[{trace}] grant failed for payment {rec.id}: {err}")
        await self.d.store.patch_meta(rec.id, {
            M_NEEDS_PROVISIONING: True,
            "provisioning_error": err[:500],
            "provisioning_attempted_at": self.d.clock().isoformat(),
        })
        text = (T.admin_gift_pending(telegram_id=rec.telegram_id, payment_id=rec.id)
                if isinstance(exc, _GiftUnavailable)
                else T.admin_not_provisioned(payment_id=rec.id, telegram_id=rec.telegram_id, error=err))
        await self.d.notifier.notify_admins(AdminTopic.PAYMENTS, text, html=True,
                                            dedup_key=f"ff:not_granted:{rec.id}", dedup_ttl=ALERT_TTL_S)
        if isinstance(exc, _GiftUnavailable) and await self.d.store.patch_meta(rec.id, {}, claim="gift_pending_told"):
            await self.d.notifier.notify_user(rec.telegram_id, T.GIFT_PENDING, html=True)
        return FulfilResult(Outcome.RETRY, rec, detail=type(exc).__name__)

    async def _hold(self, rec: PaymentRecord, reason: str, trace: str) -> None:
        store = self.d.store
        await store.patch_meta(rec.id, {
            M_NEEDS_REVIEW: True, "review_reason": reason[:500],
            "review_marked_at": rec.meta.get("review_marked_at") or self.d.clock().isoformat(),
        })
        logger.error(f"[{trace}] payment_held_for_review: id={rec.id} tg_id={rec.telegram_id} reason={reason}")
        if await store.patch_meta(rec.id, {}, claim="review_alerted"):
            sent = await self.d.notifier.notify_admins(
                AdminTopic.PAYMENTS,
                T.admin_held(payment_id=rec.id, external_id=rec.external_id, telegram_id=rec.telegram_id,
                             amount=rec.amount, currency=rec.currency, reason=reason),
                html=True, reply_markup=self.d.ui.review_admin(rec.id),
            )
            if not sent:
                await store.patch_meta(rec.id, {}, drop=("review_alerted",))
        if await store.patch_meta(rec.id, {}, claim="review_user_told"):
            await self.d.notifier.notify_user(rec.telegram_id, T.HELD_USER, html=True, reply_markup=self.d.ui.support())

    async def _card_check(self, rec: PaymentRecord, fingerprint: Optional[str], trace: str) -> None:
        if not fingerprint:
            return
        try:
            reason = await self.d.hooks.card_block_reason(fingerprint)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{trace}] card stop-list check failed: {type(e).__name__}")
            return
        if reason is not None:
            logger.warning(f"[{trace}] blocked_card_payment: payment={rec.id} fingerprint={fingerprint}")
            await self.d.notifier.notify_admins(
                AdminTopic.PAYMENTS,
                T.admin_blocked_card(external_id=rec.external_id, fingerprint=fingerprint, reason=reason),
                html=True, dedup_key=f"ff:blocked_card:{rec.id}", dedup_ttl=86400,
            )

    async def _adopt(self, external_id: str, view: dict) -> Optional[PaymentRecord]:
        meta = view.get("metadata") or {}
        tg = meta.get("tg_user_id")
        if not str(tg or "").isdigit():
            logger.error(f"webhook: payment {external_id} has no tg_user_id and no DB row, ignoring")
            return None
        plan = meta.get("plan_code") or None
        months = int(meta["period_months"]) if str(meta.get("period_months") or "").isdigit() else None
        from app.domain.plans import is_obhod_package_code

        kind = meta.get("kind") or ("obhod_package" if is_obhod_package_code(plan) else "subscription")
        logger.warning(f"webhook: adopting payment {external_id} unknown to the DB (tg_id={tg})")
        return await self.d.store.create(
            int(tg), provider="yookassa", external_id=external_id,
            amount=Decimal(str(view.get("amount") or 0)), currency=view.get("currency") or "RUB",
            status="pending", plan_code=plan, months=months, kind=kind, method="yookassa",
            description=view.get("description") or "CRS VPN",
            meta={"plan_code": plan, "period_months": months, "expected_amount": meta.get("expected_amount"),
                  "adopted_from_webhook": True},
        )

    # ------------------------------------------------------------------ admin review

    async def decide_review(self, payment_id: int, admin_id: int, approve: bool) -> str:
        """Admin decision on a held payment. Returns a key of texts.checkout.REVIEW_TEXT."""
        from app.infra.redis.locks import user_action_lock

        store = self.d.store
        async with user_action_lock("payment_review", int(payment_id)) as acquired:
            if not acquired:
                return "busy"
            rec = await store.get(payment_id)
            if rec is None:
                return "not_found"
            meta = rec.meta
            if not meta.get(M_NEEDS_REVIEW):
                return "not_held"
            if meta.get(M_REVIEW_REJECTED):
                return "already_rejected"
            if not approve:
                if meta.get(M_REVIEW_APPROVED):
                    return "already_approved"
                await store.patch_meta(rec.id, {M_REVIEW_REJECTED: True, "review_decided_by": int(admin_id),
                                                "review_decided_at": self.d.clock().isoformat()})
                logger.warning(f"payment review rejected: id={rec.id} by admin={admin_id}")
                return "rejected"
            if meta.get(M_REVIEW_APPROVED) and rec.fulfilled:
                return "already_done"
            if rec.status != "succeeded":
                return "not_paid"
            await store.patch_meta(rec.id, {M_REVIEW_APPROVED: True, "review_decided_by": int(admin_id),
                                            "review_decided_at": self.d.clock().isoformat()})
            logger.warning(f"payment review approved: id={rec.id} by admin={admin_id}")
        result = await self.process(rec.id, source="review")
        if result.outcome in (Outcome.FULFILLED, Outcome.ALREADY):
            return "approved"
        if result.outcome is Outcome.BUSY:
            return "busy"
        return "pending"

    # ------------------------------------------------------------------ recovery

    async def recover(self) -> dict:
        """Recovery sweep (worker): stale pending payments are re-checked with the
        provider, paid-but-not-granted ones are granted again."""
        now = self.d.clock()
        pending, stuck = await self.d.store.recovery_candidates(
            now, pending_age=RECOVERY_PENDING_AGE, stuck_age=RECOVERY_STUCK_AGE,
            horizon=RECOVERY_HORIZON, limit=RECOVERY_LIMIT,
        )
        stats = {"checked": 0, "fulfilled": 0, "retry": 0, "held": 0, "canceled": 0, "errors": 0}
        for rec in [*pending, *stuck]:
            stats["checked"] += 1
            try:
                result = await self.process(rec.id, source="recovery")
            except Exception as e:  # noqa: BLE001 - one bad payment must not stop the sweep
                stats["errors"] += 1
                logger.error(f"recovery: payment {rec.id} failed: {type(e).__name__}")
                continue
            key = {Outcome.FULFILLED: "fulfilled", Outcome.RETRY: "retry", Outcome.HELD: "held",
                   Outcome.CANCELED: "canceled"}.get(result.outcome)
            if key:
                stats[key] += 1
        if stats["checked"]:
            logger.info(f"recovery sweep: {stats}")
        return stats
