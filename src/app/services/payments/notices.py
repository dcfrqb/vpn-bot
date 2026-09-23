"""Messages after a payment is granted (release 3.0, A): mixed into Fulfillment.

User and admin messages are claimed in the payment meta before sending
(``notified`` / ``admin_notified``) and unclaimed when the send fails, so a
payment is announced once even when webhook, check and recovery race.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from app.domain.models import AdminTopic, SubscriptionState
from app.domain.plans import get_plan_name
from app.domain.texts import checkout as T
from app.domain.texts import months_ru
from app.logger import logger
from app.services.payments.store import M_ADMIN_NOTIFIED, M_NOTIFIED, PaymentRecord

REFUND_WINDOW = timedelta(hours=24)


def plan_label(plan_code: Optional[str], months: Optional[int]) -> str:
    name = get_plan_name(plan_code) if plan_code else "?"
    return f"{name}, {months_ru(months)}" if months else name


class FulfillmentNotices:
    """Needs ``self.d`` (MoneyDeps)."""

    # ------------------------------------------------------------------ after grant

    async def _after(self, rec: PaymentRecord, state: Optional[SubscriptionState], trace: str) -> None:
        await self.d.hooks.invalidate_caches(rec.telegram_id)
        if rec.kind in ("subscription", "autorenew"):
            await self._autorenew_after_paid(rec, trace)
        await self._notify(rec, state, trace)
        if rec.kind in ("subscription", "autorenew"):
            try:
                await self.d.hooks.after_paid(rec.id, self.d.bot)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{trace}] after_paid hook soft-fail: {type(e).__name__}")

    async def _autorenew_after_paid(self, rec: PaymentRecord, trace: str) -> None:
        if not (rec.autorenew or rec.kind == "autorenew"):
            return
        pm = rec.meta.get("payment_method") or {}
        if rec.kind == "autorenew":
            return  # the saved method is already on the subscription
        if not (isinstance(pm, dict) and pm.get("saved") and pm.get("id")):
            logger.info(f"[{trace}] autorenew requested but the card was not saved: payment={rec.id}")
            return
        try:
            mid = await self.d.store.save_method(
                rec.telegram_id, provider="yookassa", external_id=str(pm["id"]), title=pm.get("title"),
                card_fingerprint=None,
            )
            await self.d.store.set_autorenew(rec.telegram_id, True, method_id=mid)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[{trace}] autorenew enable failed: {type(e).__name__}")

    async def _expires_at(self, rec: PaymentRecord, state: Optional[SubscriptionState]) -> Optional[datetime]:
        if state is not None and state.expires_at is not None:
            return state.expires_at
        sub = await self.d.store.main_subscription(rec.telegram_id)
        return sub.valid_until if sub else None

    def refund_button_allowed(self, rec: PaymentRecord, now: Optional[datetime] = None) -> bool:
        if not getattr(self.d.settings, "REFUND_24H_ENABLED", False):
            return False
        if rec.kind != "subscription" or rec.method not in ("yookassa", "stars") or rec.status != "succeeded":
            return False
        paid = rec.paid_at or rec.created_at
        now = (now or self.d.clock()).replace(tzinfo=None)
        return paid is not None and now - paid <= REFUND_WINDOW

    async def _notify(self, rec: PaymentRecord, state: Optional[SubscriptionState], trace: str) -> None:
        store, notifier = self.d.store, self.d.notifier
        if not rec.meta.get(M_NOTIFIED) and await store.patch_meta(rec.id, {}, claim=M_NOTIFIED):
            text, markup = await self._user_message(rec, state)
            sent = await notifier.notify_user(rec.telegram_id, text, html=True, reply_markup=markup) if text else True
            if not sent:
                await store.patch_meta(rec.id, {}, drop=(M_NOTIFIED,))
        if rec.kind == "obhod_package" and rec.meta.get("obhod_package_applied") is False:
            if await store.patch_meta(rec.id, {}, claim="obhod_package_alerted"):
                await notifier.notify_admins(
                    AdminTopic.PAYMENTS,
                    T.admin_obhod_manual(telegram_id=rec.telegram_id, package=rec.plan_code or "?",
                                         external_id=rec.external_id),
                    html=True,
                )
        if not rec.meta.get(M_ADMIN_NOTIFIED) and await store.patch_meta(rec.id, {}, claim=M_ADMIN_NOTIFIED):
            text = await self._admin_message(rec, state)
            if not await notifier.notify_admins(AdminTopic.PAYMENTS, text, html=True):
                await store.patch_meta(rec.id, {}, drop=(M_ADMIN_NOTIFIED,))

    async def _user_message(self, rec: PaymentRecord, state: Optional[SubscriptionState]) -> tuple[str, Any]:
        ui = self.d.ui
        name = get_plan_name(rec.plan_code) if rec.plan_code else ""
        if rec.kind == "obhod_package":
            if rec.meta.get("obhod_package_applied"):
                return T.obhod_package_paid(), None
            return T.OBHOD_PACKAGE_MANUAL, ui.support()
        if rec.kind == "gift":
            username = await self.d.bot_username()
            code = rec.meta.get("gift_code") or ""
            link = f"https://t.me/{username}?start=g_{code}" if username else f"g_{code}"
            return T.gift_paid_buyer(name, int(rec.period_months or 1), link), ui.gift_paid()
        expires = await self._expires_at(rec, state)
        if rec.kind == "autorenew":
            return T.autorenew_paid_user(name, rec.amount, expires), ui.paid(rec.id, refund_button=False)
        return (T.paid_user(name, int(rec.period_months or 1), expires),
                ui.paid(rec.id, refund_button=self.refund_button_allowed(rec)))

    async def _admin_message(self, rec: PaymentRecord, state: Optional[SubscriptionState]) -> str:
        first, last, username = await self.d.store.user_names(rec.telegram_id)
        count, total = await self.d.store.payer_stats(rec.telegram_id)
        label = plan_label(rec.plan_code, rec.period_months)
        if rec.kind == "gift":
            label = f"Подарок: {label}"
        elif rec.kind == "autorenew":
            label = f"Автопродление: {label}"
        method = {"stars": "звезды", "saved_card": "сохраненная карта"}.get(
            "saved_card" if rec.kind == "autorenew" else rec.method, "ЮKassa")
        return T.admin_paid(
            full_name=f"{first} {last}".strip(), username=username, telegram_id=rec.telegram_id,
            plan_label=label, amount=rec.amount, currency=rec.currency, payment_number=max(count, 1),
            total_rub=total, expires_at=await self._expires_at(rec, state) if rec.kind != "gift" else None,
            external_id=rec.external_id, method=method,
        )

