"""Price rules of the money path (release 3.0, stream A). Pure functions.

The price gate runs ONCE per payment, in app.services.fulfillment, before
anything is granted: the paid amount must match the catalog price, or the
``expected_amount`` the server itself recorded when it created the payment
(so a catalog change does not hold payments that were already in flight).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from dateutil.relativedelta import relativedelta

from app.domain.plans import amounts_match, get_expected_amount


def price_mismatch_reason(
    plan_code: Optional[str],
    period_months: Optional[int],
    amount: float,
    currency: Optional[str],
    meta: Any,
) -> Optional[str]:
    """None when the paid RUB amount matches, else a reason for the admin review."""
    if currency and str(currency).upper() != "RUB":
        return f"currency={currency!r} (ожидали RUB)"
    candidates = []
    catalog = get_expected_amount(plan_code, period_months)
    if catalog > 0:
        candidates.append(catalog)
    if isinstance(meta, dict) and meta.get("expected_amount") not in (None, ""):
        try:
            recorded = int(float(meta.get("expected_amount")))
            if recorded > 0:
                candidates.append(recorded)
        except (TypeError, ValueError):
            pass
    if not candidates:
        return f"нет цены в прайсе для plan={plan_code!r} period={period_months!r}"
    if any(amounts_match(amount, c) for c in candidates):
        return None
    return (
        f"сумма {amount} не совпадает с прайсом {sorted(set(candidates))} "
        f"для plan={plan_code!r} period={period_months!r}"
    )


def stars_mismatch_reason(paid_stars: Any, expected_stars: Optional[int]) -> Optional[str]:
    try:
        paid = int(paid_stars)
    except (TypeError, ValueError):
        return f"непонятная сумма в звездах {paid_stars!r}"
    if not expected_stars or expected_stars <= 0:
        return "нет цены в звездах у платежа"
    if paid != int(expected_stars):
        return f"оплачено {paid} XTR, ожидали {int(expected_stars)} XTR"
    return None


def stars_for_rub(amount_rub: int, rate: float) -> Optional[int]:
    """Stars price for a RUB price at STARS_RATE rubles per star (ceil). None when not configured."""
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return None
    if rate <= 0 or amount_rub <= 0:
        return None
    stars = -(-int(amount_rub) * 100 // int(round(rate * 100)))
    return max(1, int(stars))


def amount_fallback_plan(amount: float) -> tuple[str, int]:
    """2.x AMOUNT FALLBACK for payments without plan metadata (dashboard payments).
    Used only after an admin approved such a payment."""
    table = ((1799, "premium", 12), (999, "premium", 6), (899, "basic", 12), (549, "premium", 3),
             (499, "basic", 6), (249, "basic", 3), (199, "premium", 1))
    for threshold, plan, months in table:
        if amount >= threshold:
            return plan, months
    return "basic", 1


def months_to_days(months: int, now: Optional[datetime] = None) -> int:
    """Calendar months from now as days (1 month from 31.01 = 28/29 days, 12 months = 365/366)."""
    now = now or datetime.now(timezone.utc)
    return ((now + relativedelta(months=int(months))) - now).days
