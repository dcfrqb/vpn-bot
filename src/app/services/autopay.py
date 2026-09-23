"""Autopay (release 3.0, stream A). Code complete, OFF: AUTOPAY_ENABLED=false.

The owner has NOT enabled YooKassa recurring payments yet (plan section 7), so
the checkout shows no autorenew line and the job does nothing until the flag
is on and a real test charge passed.

Per main subscription with autorenew=True and a saved card:
- 3 days before the end: one notice «спишем N ₽» with «Отключить» (dedup per period);
- 1 day before the end: charge the saved card for the plan and months of the
  last paid period, at today's catalog price (Quote, never a stored amount);
  a failed attempt is retried after 12 hours; after 2 failed attempts
  autorenew switches off and the user is told.
Idempotency: one period = (subscription id, valid_until). A charge carries the
Idempotence-Key ``autopay:<period>:<attempt>``, the attempt number comes from
the payments already recorded for the period, and a per-user Redis lock
serialises runs. A paid charge goes through Fulfillment like any payment.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from app.domain.models import PaymentIntent, PaymentKind, PaymentMethod, PaymentStatus
from app.domain.plans import get_plan_name
from app.domain.texts import checkout as T
from app.domain.texts import months_ru
from app.logger import logger
from app.services.payments.store import SubInfo

NOTICE_BEFORE = timedelta(days=3)
CHARGE_BEFORE = timedelta(days=1)
RETRY_AFTER = timedelta(hours=12)
MAX_FAILED_ATTEMPTS = 2
LOCK_TTL_S = 300


def period_key(sub: SubInfo) -> str:
    return f"{sub.id}:{sub.valid_until:%Y%m%d%H%M}" if sub.valid_until else f"{sub.id}:none"


class AutopayService:
    def __init__(self, deps: Any, fulfillment: Any):
        self.d = deps
        self.fulfillment = fulfillment

    def enabled(self) -> bool:
        return bool(getattr(self.d.settings, "AUTOPAY_ENABLED", False))

    # ------------------------------------------------------------------ user toggles

    async def stop(self, telegram_id: int) -> tuple[bool, Optional[datetime]]:
        """User pressed «Отключить автопродление». -> (was_on, valid_until)."""
        sub = await self.d.store.main_subscription(int(telegram_id))
        if sub is None or not sub.autorenew:
            return False, sub.valid_until if sub else None
        await self.d.store.set_autorenew(int(telegram_id), False)
        logger.info(f"autopay stopped by user: tg_id={telegram_id}")
        return True, sub.valid_until

    # ------------------------------------------------------------------ job

    async def run_once(self) -> dict:
        stats = {"notices": 0, "charged": 0, "failed": 0, "skipped": 0}
        if not self.enabled():
            return stats
        now = self.d.clock().replace(tzinfo=None)
        subs = await self.d.store.autorenew_subscriptions(until=now + NOTICE_BEFORE)
        for sub in subs:
            try:
                left = sub.valid_until - now
                if left < -CHARGE_BEFORE:
                    stats["skipped"] += 1  # ended more than a day ago: no late charge
                    continue
                if left <= CHARGE_BEFORE:
                    outcome = await self._charge(sub, now)
                    stats[outcome] = stats.get(outcome, 0) + 1
                elif await self._notice(sub):
                    stats["notices"] += 1
            except Exception as e:  # noqa: BLE001 - one user must not stop the others
                stats["failed"] += 1
                logger.error(f"autopay: subscription {sub.id} failed {type(e).__name__}: {e}")
        if any(stats.values()):
            logger.info(f"autopay run: {stats}")
        return stats

    async def _renewal_quote(self, sub: SubInfo):
        last = await self.d.store.last_paid_subscription(sub.telegram_id)
        plan = (last.plan_code if last else None) or sub.plan_code
        months = (last.period_months if last else None) or 1
        if not plan:
            return None
        return await self._checkout_quote(sub.telegram_id, plan, months)

    async def _checkout_quote(self, telegram_id: int, plan: str, months: int):
        from app.services.checkout import CheckoutServiceImpl

        return await CheckoutServiceImpl(self.d, self.fulfillment).quote(telegram_id, plan, months)

    async def _notice(self, sub: SubInfo) -> bool:
        quote = await self._renewal_quote(sub)
        if quote is None:
            await self._turn_off(sub, reason="plan is not sold any more")
            return False
        return await self.d.notifier.notify_user(
            sub.telegram_id, T.autopay_notice(get_plan_name(quote.plan_code), quote.months, quote.amount_rub),
            html=True, reply_markup=self.d.ui.autopay_notice(),
            dedup_key=f"autopay:notice:{period_key(sub)}", dedup_ttl=5 * 86400,
        )

    async def _turn_off(self, sub: SubInfo, *, reason: str, tell: Optional[str] = None) -> None:
        await self.d.store.set_autorenew(sub.telegram_id, False)
        logger.warning(f"autopay off: tg_id={sub.telegram_id} reason={reason}")
        if tell:
            await self.d.notifier.notify_user(sub.telegram_id, tell, html=True, reply_markup=self.d.ui.renew(),
                                              dedup_key=f"autopay:off:{period_key(sub)}", dedup_ttl=7 * 86400)

    async def _charge(self, sub: SubInfo, now: datetime) -> str:
        from app.infra.redis.locks import user_action_lock

        async with user_action_lock("autopay", sub.telegram_id, ttl=LOCK_TTL_S) as acquired:
            if not acquired:
                return "skipped"
            key = period_key(sub)
            attempts = await self.d.store.autorenew_attempts(sub.telegram_id, key)
            for a in attempts:
                if a.status == "succeeded":
                    if not a.fulfilled:
                        await self.fulfillment.process(a.id, source="autopay")
                    return "skipped"
                if a.status in ("pending", "waiting_for_capture"):
                    result = await self.fulfillment.process(a.id, source="autopay")
                    return "charged" if result.outcome.value in ("fulfilled", "already") else "skipped"
            failed = [a for a in attempts if a.status in ("canceled", "failed")]
            if len(failed) >= MAX_FAILED_ATTEMPTS:
                await self._turn_off(sub, reason="two failed charges", tell=T.AUTOPAY_TURNED_OFF_FAILS)
                return "failed"
            if failed and failed[-1].created_at and now - failed[-1].created_at < RETRY_AFTER:
                return "skipped"
            method = await self.d.store.get_method(sub.autorenew_method_id) if sub.autorenew_method_id else None
            if method is None or not method.is_active:
                await self._turn_off(sub, reason="no saved card", tell=T.AUTOPAY_TURNED_OFF_FAILS)
                return "failed"
            quote = await self._renewal_quote(sub)
            if quote is None:
                await self._turn_off(sub, reason="plan is not sold any more", tell=T.AUTOPAY_TURNED_OFF_FAILS)
                return "failed"
            attempt = len(failed) + 1
            description = f"CRS VPN {quote.title}, {months_ru(quote.months)}, автопродление".replace(" ", " ")
            intent = PaymentIntent(plan_code=quote.plan_code, months=quote.months, amount_rub=quote.amount_rub,
                                   kind=PaymentKind.AUTORENEW, method=PaymentMethod.SAVED_CARD,
                                   telegram_id=sub.telegram_id, autorenew=True)
            try:
                intent = await self.d.payments.charge_saved_method(
                    intent, payment_method_id=method.external_id, description=description,
                    idempotence_key=f"autopay:{key}:{attempt}",
                )
            except Exception as e:  # noqa: BLE001 - provider down: retried on the next run
                logger.error(f"autopay: charge call failed tg_id={sub.telegram_id}: {type(e).__name__}")
                return "failed"
            rec = await self.d.store.create(
                sub.telegram_id, provider="yookassa", external_id=intent.external_id,
                amount=Decimal(quote.amount_rub), currency="RUB", status="pending", plan_code=quote.plan_code,
                months=quote.months, kind="autorenew", method="saved_card", description=description,
                meta={"plan_code": quote.plan_code, "period_months": quote.months,
                      "expected_amount": quote.amount_rub, "period_key": key, "attempt": attempt,
                      "autorenew": True, "kind": "autorenew"},
            )
            if intent.status is PaymentStatus.CANCELED:
                await self.d.store.set_status(rec.id, ("pending",), "canceled")
                return await self._failed(sub, attempt)
            result = await self.fulfillment.process(rec.id, source="autopay")
            if result.outcome.value == "canceled":
                return await self._failed(sub, attempt)
            return "charged" if result.outcome.value in ("fulfilled", "already") else "skipped"

    async def _failed(self, sub: SubInfo, attempt: int) -> str:
        if attempt >= MAX_FAILED_ATTEMPTS:
            await self._turn_off(sub, reason="two failed charges", tell=T.AUTOPAY_TURNED_OFF_FAILS)
        else:
            await self.d.notifier.notify_user(sub.telegram_id, T.AUTOPAY_FAILED, html=True,
                                              reply_markup=self.d.ui.renew(),
                                              dedup_key=f"autopay:failed:{period_key(sub)}:{attempt}",
                                              dedup_ttl=7 * 86400)
        return "failed"
