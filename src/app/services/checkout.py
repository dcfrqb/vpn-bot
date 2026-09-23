"""Checkout (release 3.0, stream A) and the 2.x server price resolver.

``resolve_purchase_amount``: 2.x helper (create_payment of the old screens and
the legacy router still call it). The rule itself is domain.plans.quote_purchase.

``CheckoutServiceImpl``: app.services.ports.CheckoutService.
  quote()  server price for this user (catalog only; legacy plan only for its owner),
           plus the Stars price when STARS_ENABLED and STARS_RATE > 0;
  start()  create a payment, or reuse the user's pending one for the same
           (plan, months, kind, method, autorenew) created in the last 15 minutes;
  check()  «Проверить оплату»: provider status + fulfillment.
Prices never come from callers: ``start`` re-quotes and refuses a stale Quote.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Any, Mapping, Optional

from app.core.plans import LEGACY_PLAN_CODES, quote_purchase
from app.logger import logger


async def resolve_purchase_amount(
    plan_code: Optional[str],
    period_months: Optional[int],
    user_id: int,
    *,
    allow_obhod_package: bool = False,
) -> int:
    """Цена покупки для юзера или 0 (продавать нельзя). См. core/plans.quote_purchase."""
    code = (plan_code or "").lower().strip()
    last_plan = None
    if code in LEGACY_PLAN_CODES:
        try:
            from app.services.users import get_user_last_plan
            last_plan = await get_user_last_plan(int(user_id))
        except Exception as e:
            # fail-closed: без last_plan legacy-тариф не продаем
            logger.warning(f"resolve_purchase_amount: get_user_last_plan failed user={user_id} err={e}")
    return quote_purchase(
        code, period_months, last_plan=last_plan, allow_obhod_package=allow_obhod_package
    )


# ---------------------------------------------------------------------------
# 3.0 CheckoutService
# ---------------------------------------------------------------------------

REUSE_WINDOW = timedelta(minutes=15)
CHECKOUT_LOCK_TTL = 30


@dataclass
class StartResult:
    """start_checkout outcome. ``error`` in: unavailable, blocked, busy, create_failed,
    stars_disabled."""

    intent: Optional[Any] = None  # PaymentIntent
    error: Optional[str] = None
    reused: bool = False

    @property
    def ok(self) -> bool:
        return self.intent is not None and self.error is None


class CheckoutServiceImpl:
    def __init__(self, deps: Any, fulfillment: Any):
        self.d = deps
        self.fulfillment = fulfillment

    # ------------------------------------------------------------------ quotes

    def _stars_price(self, amount_rub: int) -> Optional[int]:
        from app.services.payments.pricing import stars_for_rub

        s = self.d.settings
        if not getattr(s, "STARS_ENABLED", False):
            return None
        return stars_for_rub(amount_rub, getattr(s, "STARS_RATE", 0) or 0)

    async def quote(self, telegram_id: int, plan_code: str, months: int, *, gift: bool = False):
        from app.domain.models import Quote
        from app.domain.plans import get_plan_name

        code = (plan_code or "").lower().strip()
        last_plan = None
        if code in LEGACY_PLAN_CODES and not gift:
            try:
                last_plan = await self.d.hooks.last_plan(int(telegram_id))
            except Exception as e:  # noqa: BLE001 - fail closed: no legacy sale without last_plan
                logger.warning(f"checkout.quote: last_plan failed user={telegram_id} ({type(e).__name__})")
        try:
            m = int(months)
        except (TypeError, ValueError):
            return None
        amount = quote_purchase(code, m, last_plan=last_plan)
        if amount <= 0:
            return None
        return Quote(
            plan_code=code, months=m, amount_rub=int(amount), title=get_plan_name(code),
            stars=self._stars_price(int(amount)), is_legacy=code in LEGACY_PLAN_CODES,
        )

    async def plan_options(self, telegram_id: int, *, gift: bool = False) -> list[tuple[str, str, tuple, int]]:
        """[(code, name, features, 1-month price)] the user may buy: menu plans, plus
        their own legacy plan for a renewal (not for gifts)."""
        from app.domain.plans import MENU_PLAN_CODES, get_plan_features, get_plan_name

        codes = list(MENU_PLAN_CODES)
        if not gift:
            try:
                last = await self.d.hooks.last_plan(int(telegram_id))
            except Exception:  # noqa: BLE001
                last = None
            if last in LEGACY_PLAN_CODES:
                codes.append(last)
        out = []
        for code in codes:
            periods = await self.period_options(telegram_id, code, gift=gift)
            if periods:
                out.append((code, get_plan_name(code), tuple(get_plan_features(code)), min(p[1] for p in periods)))
        return out

    async def period_options(self, telegram_id: int, plan_code: str, *, gift: bool = False) -> list[tuple[int, int, int]]:
        """[(months, price, saving %)] sellable periods of a plan for this user."""
        from app.domain.plans import PLAN_CATALOG

        meta = PLAN_CATALOG.get((plan_code or "").lower().strip())
        if not meta:
            return []
        out = []
        base = None
        for months in sorted(meta["prices"]):
            q = await self.quote(telegram_id, plan_code, months, gift=gift)
            if q is None:
                continue
            if months == 1:
                base = q.amount_rub
            saving = 0
            if base and months > 1:
                saving = max(0, int(round((1 - q.amount_rub / (base * months)) * 100)))
            out.append((months, q.amount_rub, saving))
        return out

    # ------------------------------------------------------------------ start

    async def start(self, telegram_id: int, quote: Any, *, method: str = "yookassa", autorenew: bool = False,
                    kind: str = "subscription", user: Optional[Mapping[str, Any]] = None):
        """CheckoutService port: raises ValueError when checkout is refused (use
        ``start_checkout`` for a result object)."""
        res = await self.start_checkout(telegram_id, quote, method=method, autorenew=autorenew, kind=kind, user=user)
        if not res.ok:
            raise ValueError(f"checkout refused: {res.error}")
        return res.intent

    async def start_checkout(self, telegram_id: int, quote: Any, *, method: str = "yookassa",
                             autorenew: bool = False, kind: str = "subscription",
                             user: Optional[Mapping[str, Any]] = None) -> StartResult:
        from app.infra.redis.locks import user_action_lock

        tg = int(telegram_id)
        gift = kind == "gift"
        fresh = await self.quote(tg, quote.plan_code, quote.months, gift=gift)
        if fresh is None or fresh.amount_rub != quote.amount_rub:
            return StartResult(error="unavailable")
        if method == "stars" and not fresh.stars:
            return StartResult(error="stars_disabled")
        autorenew = bool(autorenew) and not gift and method == "yookassa" and bool(
            getattr(self.d.settings, "AUTOPAY_ENABLED", False))
        try:
            reason = await self.d.hooks.user_block_reason(tg)
        except Exception:  # noqa: BLE001 - stop-list errors never block a sale (2.x)
            reason = None
        if reason is not None:
            logger.warning(f"blocked_user_payment_attempt: tg_id={tg} plan={fresh.plan_code}")
            from app.domain.models import AdminTopic

            await self.d.notifier.notify_admins(
                AdminTopic.PAYMENTS,
                f"⛔ Заблокированный пользователь пытался оплатить: ID {tg}, тариф {fresh.plan_code}, "
                f"причина: {reason or '-'}",
                dedup_key=f"blocked_pay:{tg}", dedup_ttl=86400,
            )
            return StartResult(error="blocked")
        async with user_action_lock("checkout", tg, ttl=CHECKOUT_LOCK_TTL) as acquired:
            if not acquired:
                return StartResult(error="busy")
            now = self.d.clock()
            reuse = await self.d.store.find_reusable(
                tg, plan_code=fresh.plan_code, months=fresh.months, kind=kind, method=method,
                autorenew=autorenew, since=now - REUSE_WINDOW,
            )
            if reuse is not None and self._reuse_matches(reuse, fresh, method):
                intent = self._intent(reuse, fresh, autorenew)
                if method == "stars":
                    ok = await self._send_invoice(tg, reuse.id, intent, gift=gift)
                    if not ok:
                        return StartResult(error="create_failed")
                logger.info(f"checkout reuse: payment={reuse.id} tg_id={tg}")
                return StartResult(intent=intent, reused=True)
            if method == "stars":
                return await self._start_stars(tg, fresh, kind=kind, user=user)
            return await self._start_yookassa(tg, fresh, kind=kind, autorenew=autorenew, user=user, now=now)

    @staticmethod
    def _reuse_matches(rec: Any, quote: Any, method: str) -> bool:
        if method == "stars":
            return rec.expected_stars == quote.stars
        try:
            return int(float(rec.meta.get("expected_amount") or rec.amount)) == quote.amount_rub
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _intent(rec: Any, quote: Any, autorenew: bool):
        from app.domain.models import PaymentIntent, PaymentKind, PaymentMethod, PaymentStatus

        return PaymentIntent(
            plan_code=quote.plan_code, months=quote.months, amount_rub=quote.amount_rub,
            kind=PaymentKind(rec.kind) if rec.kind in PaymentKind._value2member_map_ else PaymentKind.SUBSCRIPTION,
            method=PaymentMethod.STARS if rec.method == "stars" else PaymentMethod.YOOKASSA,
            telegram_id=rec.telegram_id, payment_id=rec.id, external_id=rec.external_id,
            status=PaymentStatus.PENDING, autorenew=autorenew, stars=rec.expected_stars,
            confirmation_url=rec.confirmation_url, created_at=rec.created_at,
        )

    async def _start_yookassa(self, tg: int, quote: Any, *, kind: str, autorenew: bool,
                              user: Optional[Mapping[str, Any]], now=None) -> StartResult:
        from app.domain.models import PaymentIntent, PaymentKind
        from app.domain.texts import months_ru

        # One key per checkout attempt: the client repeats it on network/5xx retries,
        # so YooKassa never creates two payments for one attempt. A new attempt
        # (after the old one was paid or expired) must get a NEW payment, so the
        # key is not derived from (user, plan, months): the pending reuse above
        # already covers double clicks.
        key = f"co:{tg}:{uuid.uuid4().hex}"
        description = f"CRS VPN {quote.title}, {months_ru(quote.months)}".replace(" ", " ")
        if kind == "gift":
            description = f"Подарок: {description}"
        metadata = {
            "tg_user_id": tg, "plan_code": quote.plan_code, "period_months": quote.months,
            "expected_amount": quote.amount_rub, "kind": kind, "autorenew": int(autorenew),
        }
        intent = PaymentIntent(plan_code=quote.plan_code, months=quote.months, amount_rub=quote.amount_rub,
                               kind=PaymentKind(kind), telegram_id=tg, autorenew=autorenew)
        try:
            intent = await self.d.payments.create_payment(
                intent, description=description, idempotence_key=key, save_payment_method=autorenew,
                metadata=metadata,
            )
        except Exception as e:  # noqa: BLE001 - never shown to the user
            logger.error(f"checkout: create_payment failed tg_id={tg} plan={quote.plan_code}: {type(e).__name__}: {e}")
            return StartResult(error="create_failed")
        if not intent.external_id or not intent.confirmation_url:
            logger.error(f"checkout: provider returned no id/url tg_id={tg}")
            return StartResult(error="create_failed")
        rec = await self.d.store.create(
            tg, provider="yookassa", external_id=intent.external_id, amount=Decimal(quote.amount_rub),
            currency="RUB", status="pending", plan_code=quote.plan_code, months=quote.months, kind=kind,
            method="yookassa", description=description,
            meta={"plan_code": quote.plan_code, "period_months": quote.months,
                  "expected_amount": quote.amount_rub, "confirmation_url": intent.confirmation_url,
                  "autorenew": autorenew, "kind": kind, "idempotence_key": key},
            user=user,
        )
        logger.info(f"checkout: payment created id={rec.id} ext={rec.external_id} tg_id={tg} "
                    f"plan={quote.plan_code} months={quote.months} amount={quote.amount_rub} kind={kind}")
        return StartResult(intent=replace(intent, payment_id=rec.id, created_at=rec.created_at))

    async def _start_stars(self, tg: int, quote: Any, *, kind: str, user: Optional[Mapping[str, Any]]) -> StartResult:
        from app.domain.models import PaymentIntent, PaymentKind, PaymentMethod

        ext = f"stars:{uuid.uuid4().hex}"
        rec = await self.d.store.create(
            tg, provider="stars", external_id=ext, amount=Decimal(int(quote.stars)), currency="XTR",
            status="pending", plan_code=quote.plan_code, months=quote.months, kind=kind, method="stars",
            description=f"CRS VPN {quote.title}",
            meta={"plan_code": quote.plan_code, "period_months": quote.months, "expected_stars": int(quote.stars),
                  "amount_rub": quote.amount_rub, "kind": kind},
            user=user,
        )
        intent = PaymentIntent(plan_code=quote.plan_code, months=quote.months, amount_rub=quote.amount_rub,
                               kind=PaymentKind(kind), method=PaymentMethod.STARS, telegram_id=tg,
                               payment_id=rec.id, external_id=ext, stars=int(quote.stars))
        if not await self._send_invoice(tg, rec.id, intent, gift=kind == "gift"):
            await self.d.store.set_status(rec.id, ("pending",), "canceled", {"invoice_failed": True})
            return StartResult(error="create_failed")
        return StartResult(intent=intent)

    async def _send_invoice(self, tg: int, payment_id: int, intent: Any, *, gift: bool) -> bool:
        from app.domain.plans import get_plan_name
        from app.domain.texts import checkout as T

        name = get_plan_name(intent.plan_code)
        try:
            await self.d.stars.send_invoice(
                tg, intent, title=T.stars_invoice_title(name),
                description=T.stars_invoice_description(name, intent.months, gift=gift),
                payload=stars_payload(payment_id),
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"checkout: stars invoice failed tg_id={tg} payment={payment_id}: {type(e).__name__}")
            return False

    # ------------------------------------------------------------------ check

    async def check(self, telegram_id: int, payment_id: int):
        """CheckoutService port: the payment as a PaymentIntent after re-checking it."""
        from app.domain.models import PaymentIntent, PaymentStatus

        result = await self.check_result(telegram_id, payment_id)
        rec = result.payment
        if rec is None:
            return PaymentIntent(plan_code="", months=0, amount_rub=0, telegram_id=int(telegram_id),
                                 payment_id=int(payment_id), status=PaymentStatus.FAILED)
        status = {"succeeded": PaymentStatus.SUCCEEDED, "canceled": PaymentStatus.CANCELED,
                  "refunded": PaymentStatus.REFUNDED, "failed": PaymentStatus.FAILED}.get(rec.status,
                                                                                         PaymentStatus.PENDING)
        return PaymentIntent(plan_code=rec.plan_code or "", months=int(rec.period_months or 0),
                             amount_rub=int(rec.amount), telegram_id=rec.telegram_id, payment_id=rec.id,
                             external_id=rec.external_id, status=status)

    async def check_result(self, telegram_id: int, payment_id: int = 0, *, external_id: str = ""):
        from app.services.fulfillment import FulfilResult, Outcome

        store = self.d.store
        rec = await (store.get(int(payment_id)) if payment_id else store.get_by_external(external_id))
        if rec is None or rec.telegram_id != int(telegram_id):
            return FulfilResult(Outcome.NOT_FOUND)
        return await self.fulfillment.process(rec.id, source="check")

    # ------------------------------------------------------------------ Stars pre-checkout

    async def precheck_stars(self, telegram_id: int, payload: str, total_amount: int, currency: str) -> Optional[str]:
        """pre_checkout_query: None = ok, else a user-facing error text."""
        from app.domain.texts import checkout as T

        pid = parse_stars_payload(payload)
        rec = await self.d.store.get(pid) if pid else None
        if (rec is None or rec.telegram_id != int(telegram_id) or rec.method != "stars"
                or rec.status != "pending" or str(currency).upper() != "XTR"):
            return T.PRECHECK_STALE
        fresh = await self.quote(rec.telegram_id, rec.plan_code or "", rec.period_months or 0, gift=rec.kind == "gift")
        if fresh is None or not fresh.stars:
            return T.PRECHECK_STALE
        if int(total_amount) != rec.expected_stars or fresh.stars != rec.expected_stars:
            return T.PRECHECK_PRICE_CHANGED
        return None


STARS_PAYLOAD_PREFIX = "p:"


def stars_payload(payment_id: int) -> str:
    return f"{STARS_PAYLOAD_PREFIX}{int(payment_id)}"


def parse_stars_payload(payload: Optional[str]) -> Optional[int]:
    if not payload or not payload.startswith(STARS_PAYLOAD_PREFIX):
        return None
    tail = payload[len(STARS_PAYLOAD_PREFIX):]
    return int(tail) if tail.isdigit() else None
