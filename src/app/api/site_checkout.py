"""Authenticated internal checkout bridge. The website supplies its session identity."""

from uuid import UUID
from functools import wraps

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.internal_site import guard
from app.services.site_commerce import SiteCommerceError, commerce
from app.services.site_store import SiteBusy
from app.logger import logger

router = APIRouter(prefix="/internal/site", dependencies=[Depends(guard)])


def safe_endpoint(fn):
    @wraps(fn)
    async def wrapped(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except (SiteCommerceError, SiteBusy):
            raise
        except Exception as exc:
            # Provider/SQL exception strings may contain private payment URLs or parameters.
            logger.warning("site endpoint={} error={}", fn.__name__, type(exc).__name__)
            raise SiteCommerceError("checkout_unavailable") from None

    return wrapped


class QuoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: UUID
    telegram_id: int | None = Field(None, gt=0)
    plan_code: str = Field(min_length=1, max_length=32)
    months: int = Field(ge=1, le=12)


class StartIn(QuoteIn):
    order_id: UUID
    expected_amount_rub: int = Field(gt=0)


async def error_handler(request, exc):
    if isinstance(exc, SiteBusy):
        return JSONResponse(status_code=409, content={"error": "checkout_busy"})
    return JSONResponse(
        status_code=exc.status, content={"error": exc.code, **exc.extra}
    )


@router.get("/catalog")
@safe_endpoint
async def catalog():
    from app.domain.plans import COUNTRIES, MENU_PLAN_CODES, PLAN_CATALOG
    from app.config import settings

    plans = []
    for code in MENU_PLAN_CODES:
        p = PLAN_CATALOG[code]
        plans.append(
            {
                "code": code,
                "title": p["display"],
                "device_limit": p["device_limit"],
                "countries": [COUNTRIES[c][1] for c in p["countries"]],
                "prices": p["prices"],
                "ru_entry_gb": p.get("obhod_gb"),
                "highlighted": bool(p.get("popular")),
            }
        )
    return {"plans": plans, "enabled": settings.SITE_CHECKOUT_ENABLED}


@router.post("/checkout/quote")
@safe_endpoint
async def quote(body: QuoteIn):
    return await commerce().quote(
        body.account_id, body.telegram_id, body.plan_code, body.months
    )


@router.post("/checkout/orders")
@safe_endpoint
async def start(body: StartIn):
    return await commerce().start(
        body.account_id,
        body.telegram_id,
        body.plan_code,
        body.months,
        body.order_id,
        body.expected_amount_rub,
    )


@router.get("/checkout/accounts/{account_id}/orders/{order_id}")
@safe_endpoint
async def order(account_id: UUID, order_id: UUID):
    return await commerce().refresh(account_id, order_id)


@router.get("/accounts/{account_id}/profile")
@safe_endpoint
async def profile(account_id: UUID, telegram_id: int | None = Query(None, gt=0)):
    service = commerce()
    customer = await service.store.customer(str(account_id))
    if customer is None or customer["backend"] == "telegram":
        tg = customer["telegram_id"] if customer else telegram_id
        if customer and telegram_id not in (None, tg):
            raise SiteCommerceError("account_conflict", 409)
        if tg:
            from app.services.site_profile import get_profile

            return await get_profile(tg)
        return None
    from app.domain.plans import get_plan_name

    orders = await service.store.orders(str(account_id))
    payments = [
        {
            "id": o["id"],
            "created_at": o["created_at"],
            "paid_at": o.get("paid_at"),
            "provider": "yookassa",
            "status": "succeeded"
            if o["state"] in {"paid", "provisioning", "fulfilled"}
            else o["state"],
            "amount_rub": o["amount_rub"],
            "plan_code": o["plan_code"],
            "period_months": o["months"],
            "kind": "subscription",
        }
        for o in orders
    ]
    accounts = []
    code = customer.get("plan_code")
    for kind in ["main", "obhod"]:
        panel_id = customer.get(kind + "_panel_id")
        if panel_id:
            accounts.append(
                {
                    "kind": kind,
                    "remna_id": panel_id,
                    "plan_code": code,
                    "plan_title": get_plan_name(code) if code else None,
                }
            )
    paid = [p for p in payments if p["status"] == "succeeded"]
    return {
        "user": {
            "telegram_id": None,
            "customer_since": orders[-1]["created_at"] if orders else None,
        },
        "accounts": accounts,
        "payments": payments,
        "stats": {
            "payments_count": len(paid),
            "paid_total_rub": sum(p["amount_rub"] for p in paid),
            "first_payment_at": paid[-1]["paid_at"] if paid else None,
            "last_payment_at": paid[0]["paid_at"] if paid else None,
        },
    }
