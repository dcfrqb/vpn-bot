"""Plan -> Period -> «Оплатить N ₽», PayCheck, PayStars, AutoPay, Stars pre_checkout /
successful_payment, gift purchase. Owner stream: A (Money).

Thin handlers: callbacks from app.bot.callbacks, services from
app.services.money (built over the DI container), screens from
app.bot.views.money, texts from app.domain.texts.checkout. No prices here:
every amount comes from CheckoutService quotes. Legacy ``pay_yookassa_*``
buttons arrive here as Period (the amount in the old string is ignored),
``check_payment:<id>`` as PayCheck(ext=<id>), ``buy_subscription`` as Nav(plans).
"""
from __future__ import annotations

from typing import Any, Optional

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

from app.bot.callbacks import AutoPay, Gift, Nav, PayCheck, PayStars, Period, Plan
from app.bot.views import render
from app.bot.views.money import (
    PeriodOption,
    PlanOption,
    checkout_view,
    message_view,
    periods_view,
    plans_view,
    support_url,
)
from app.domain.plans import get_plan_features, get_plan_name
from app.domain.texts import checkout as T
from app.logger import logger
from app.services.money import money

router = Router(name="r3_checkout")

CTX_TTL_S = 15 * 60


def _flag(container: Any, name: str) -> bool:
    return bool(getattr(container.settings, name, False))


def _user(cb: CallbackQuery) -> dict:
    u = cb.from_user
    return {"username": u.username, "first_name": u.first_name, "last_name": u.last_name}


async def _save_ctx(tg: int, plan: str, months: int, kind: str) -> None:
    from app.infra.redis.cache import set_json

    await set_json(f"checkout:ctx:{tg}", {"c": plan, "m": int(months), "k": kind}, ttl=CTX_TTL_S)


async def _load_ctx(tg: int) -> Optional[dict]:
    from app.infra.redis.cache import get_json

    data = await get_json(f"checkout:ctx:{tg}")
    return data if isinstance(data, dict) else None


# --- plans and periods ---------------------------------------------------------------------


async def _show_plans(cb: CallbackQuery, container: Any, *, gift: bool) -> None:
    m = money(container)
    options = [PlanOption(code=c, name=n, features=f, from_rub=p)
               for c, n, f, p in await m.checkout.plan_options(cb.from_user.id, gift=gift)]
    text, markup = plans_view(options, gifts=_flag(container, "GIFTS_ENABLED"), gift=gift)
    await render(cb, text, markup)


async def _show_periods(cb: CallbackQuery, container: Any, plan: str, *, gift: bool) -> None:
    m = money(container)
    options = [PeriodOption(months=mm, amount_rub=a, saving_percent=s)
               for mm, a, s in await m.checkout.period_options(cb.from_user.id, plan, gift=gift)]
    if not options:
        text, markup = message_view(T.PLAN_UNAVAILABLE)
        await render(cb, text, markup)
        return
    text, markup = periods_view(plan, get_plan_name(plan), get_plan_features(plan), options, gift=gift)
    await render(cb, text, markup)


@router.callback_query(Nav.filter(F.s == "plans"))
async def on_plans(cb: CallbackQuery, container: Any) -> None:
    await _show_plans(cb, container, gift=False)


@router.callback_query(Plan.filter())
async def on_plan(cb: CallbackQuery, callback_data: Plan, container: Any) -> None:
    await _show_periods(cb, container, callback_data.c, gift=False)


# --- checkout ------------------------------------------------------------------------------

_START_ERRORS = {
    "unavailable": T.PLAN_UNAVAILABLE,
    "blocked": T.PAYMENT_BLOCKED,
    "busy": T.PAYMENT_BUSY,
    "create_failed": T.PAYMENT_CREATE_FAILED,
    "stars_disabled": T.STARS_UNAVAILABLE,
}


async def _checkout(cb: CallbackQuery, container: Any, plan: str, months: int, *, kind: str,
                    autorenew: bool = False) -> None:
    m = money(container)
    tg = cb.from_user.id
    gift = kind == "gift"
    quote = await m.checkout.quote(tg, plan, months, gift=gift)
    if quote is None:
        text, markup = message_view(T.PLAN_UNAVAILABLE)
        await render(cb, text, markup)
        return
    res = await m.checkout.start_checkout(tg, quote, autorenew=autorenew, kind=kind, user=_user(cb))
    if not res.ok:
        if res.error == "busy":
            await cb.answer(T.PAYMENT_BUSY)
            return
        text, markup = message_view(_START_ERRORS.get(res.error or "", T.PAYMENT_CREATE_FAILED),
                                    support=support_url(container.settings))
        await render(cb, text, markup)
        return
    await _save_ctx(tg, quote.plan_code, quote.months, kind)
    autopay_line = None if gift or not _flag(container, "AUTOPAY_ENABLED") else bool(res.intent.autorenew)
    text, markup = checkout_view(
        plan_code=quote.plan_code, name=quote.title, months=quote.months, amount_rub=quote.amount_rub,
        payment_id=res.intent.payment_id, url=res.intent.confirmation_url, autorenew=autopay_line,
        stars=quote.stars, gift=gift,
    )
    await render(cb, text, markup)


@router.callback_query(Period.filter())
async def on_period(cb: CallbackQuery, callback_data: Period, container: Any) -> None:
    await _checkout(cb, container, callback_data.c, callback_data.m, kind="subscription")


@router.callback_query(AutoPay.filter(F.a.in_({"on", "off"})))
async def on_autopay_toggle(cb: CallbackQuery, callback_data: AutoPay, container: Any) -> None:
    if not _flag(container, "AUTOPAY_ENABLED"):
        await cb.answer(T.AUTOPAY_UNAVAILABLE, show_alert=True)
        return
    ctx = await _load_ctx(cb.from_user.id)
    if not ctx or ctx.get("k") != "subscription":
        await cb.answer(T.PRECHECK_STALE, show_alert=True)
        return
    await _checkout(cb, container, ctx["c"], int(ctx["m"]), kind="subscription", autorenew=callback_data.a == "on")


@router.callback_query(AutoPay.filter(F.a == "stop"))
async def on_autopay_stop(cb: CallbackQuery, container: Any) -> None:
    was_on, until = await money(container).autopay.stop(cb.from_user.id)
    text = T.autopay_stopped(until) if was_on else T.AUTOPAY_NOTHING_TO_STOP
    await cb.answer()
    await cb.message.answer(text)


@router.callback_query(AutoPay.filter(F.a == "info"))
async def on_autopay_info(cb: CallbackQuery) -> None:
    text, markup = message_view(T.AUTOPAY_INFO)
    await render(cb, text, markup)


# --- payment check -------------------------------------------------------------------------


@router.callback_query(PayCheck.filter())
async def on_pay_check(cb: CallbackQuery, callback_data: PayCheck, container: Any) -> None:
    from app.services.cache import check_payment_rate_limit
    from app.services.fulfillment import Outcome

    tg = cb.from_user.id
    key = str(callback_data.pid or callback_data.ext)
    allowed, wait = await check_payment_rate_limit(tg, key)
    if not allowed:
        await cb.answer(T.check_rate_limited(wait), show_alert=True)
        return
    m = money(container)
    result = await m.checkout.check_result(tg, callback_data.pid, external_id=callback_data.ext)
    rec = result.payment
    support = support_url(container.settings)
    o = result.outcome
    if o in (Outcome.FULFILLED, Outcome.ALREADY):
        text = T.CHECK_GIFT_PAID if rec is not None and rec.kind == "gift" else T.CHECK_PAID
        view = message_view(text, back_to_plans=False, connect=rec is None or rec.kind != "gift")
    elif o is Outcome.PENDING:
        view = message_view(T.CHECK_PENDING, pay_url=rec.confirmation_url if rec else None,
                            amount_rub=int(rec.amount) if rec and rec.currency == "RUB" else None,
                            check_pid=rec.id if rec else None)
    elif o is Outcome.HELD:
        view = message_view(T.CHECK_HELD, back_to_plans=False, support=support)
    elif o is Outcome.REJECTED:
        view = message_view(T.CHECK_REJECTED, back_to_plans=False, support=support)
    elif o is Outcome.CANCELED:
        view = message_view(T.CHECK_CANCELED)
    elif o is Outcome.REFUNDED:
        view = message_view(T.CHECK_REFUNDED, support=support)
    elif o is Outcome.NOT_FOUND:
        view = message_view(T.CHECK_NOT_FOUND)
    elif rec is not None and rec.status == "succeeded":  # BUSY / RETRY after the money arrived
        view = message_view(T.CHECK_PROVISIONING, back_to_plans=False, support=support)
    else:
        view = message_view(T.CHECK_ERROR, check_pid=rec.id if rec else None)
    logger.info(f"pay check: tg_id={tg} payment={rec.id if rec else key} outcome={o.value}")
    await render(cb, *view)


# --- Telegram Stars ------------------------------------------------------------------------


@router.callback_query(PayStars.filter())
async def on_pay_stars(cb: CallbackQuery, callback_data: PayStars, container: Any) -> None:
    if not _flag(container, "STARS_ENABLED"):
        await cb.answer(T.STARS_UNAVAILABLE, show_alert=True)
        return
    m = money(container)
    quote = await m.checkout.quote(cb.from_user.id, callback_data.c, callback_data.m)
    if quote is None or not quote.stars:
        await cb.answer(T.STARS_UNAVAILABLE, show_alert=True)
        return
    res = await m.checkout.start_checkout(cb.from_user.id, quote, method="stars", user=_user(cb))
    if not res.ok:
        await cb.answer(_START_ERRORS.get(res.error or "", T.PAYMENT_CREATE_FAILED), show_alert=True)
        return
    await cb.answer(T.STARS_INVOICE_SENT)


@router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery, container: Any) -> None:
    error: Optional[str]
    if not _flag(container, "STARS_ENABLED"):
        error = T.STARS_UNAVAILABLE
    else:
        error = await money(container).checkout.precheck_stars(
            q.from_user.id, q.invoice_payload, q.total_amount, q.currency)
    if error:
        logger.warning(f"stars pre_checkout refused: tg_id={q.from_user.id} payload={q.invoice_payload}")
        await q.answer(ok=False, error_message=error)
    else:
        await q.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, container: Any) -> None:
    from app.services.checkout import parse_stars_payload

    sp = message.successful_payment
    pid = parse_stars_payload(sp.invoice_payload)
    logger.info(f"stars successful_payment: tg_id={message.from_user.id} payment={pid} "
                f"amount={sp.total_amount} {sp.currency}")
    await money(container).fulfillment.on_stars_paid(
        telegram_id=message.from_user.id, payment_id=pid or 0, charge_id=sp.telegram_payment_charge_id,
        total_amount=sp.total_amount, currency=sp.currency,
    )


# --- gifts ---------------------------------------------------------------------------------


@router.callback_query(Gift.filter(F.a.in_({"buy", "plan", "period"})))
async def on_gift(cb: CallbackQuery, callback_data: Gift, container: Any) -> None:
    if not _flag(container, "GIFTS_ENABLED"):
        await cb.answer(T.GIFTS_UNAVAILABLE, show_alert=True)
        return
    if callback_data.a == "buy":
        await _show_plans(cb, container, gift=True)
    elif callback_data.a == "plan":
        await _show_periods(cb, container, callback_data.id, gift=True)
    else:
        plan, _, months = callback_data.id.partition(".")
        if not months.isdigit():
            await cb.answer(T.PLAN_UNAVAILABLE, show_alert=True)
            return
        await _checkout(cb, container, plan, int(months), kind="gift")
