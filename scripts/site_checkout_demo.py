"""Loopback-only checkout demo: real service/routes, fake provider and panel, no live IO.

PYTHONPATH=src:. .venv/bin/uvicorn scripts.site_checkout_demo:app --host 127.0.0.1 --port 18050
All fake state is intentionally lost when this demo process stops.
"""

from datetime import datetime, timezone
from html import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api import site_checkout
from app.api.internal_site import InternalApiError, internal_api_error_handler
from app.config import settings
from app.services.site_commerce import SiteCommerceError
from app.services.site_store import SiteBusy
from tests.test_site_commerce import service

ORIGIN = "http://127.0.0.1:13040"
BANK = "http://127.0.0.1:18050"
settings.BOT_INTERNAL_TOKEN = "local-checkout-demo"
s = service()
s.clock = lambda: datetime.now(timezone.utc)
s.settings.SITE_PUBLIC_ORIGIN = ORIGIN
site_checkout.commerce = lambda: s
original_public = s.public


def public(order):
    data = original_public(order)
    if data.get("confirmation_url"):
        data["confirmation_url"] = BANK + "/demo/pay/" + order["id"]
    return data


s.public = public
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_exception_handler(InternalApiError, internal_api_error_handler)
app.add_exception_handler(SiteCommerceError, site_checkout.error_handler)
app.add_exception_handler(SiteBusy, site_checkout.error_handler)
app.include_router(site_checkout.router)


@app.middleware("http")
async def loopback_only(request: Request, call_next):
    if request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403)
    return await call_next(request)


@app.get("/demo/pay/{key}", response_class=HTMLResponse)
async def bank(key: str):
    order = await s.store.order(key)
    if not order:
        raise HTTPException(404)
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>CRS VPN: тестовая оплата</title><style>body{{font:20px system-ui;max-width:640px;margin:8vh auto;padding:24px}}button,a{{font:inherit;display:block;margin:24px 0;padding:15px;border:1px solid;border-radius:30px}}button{{background:#111;color:white;cursor:pointer}}</style>
<h1>Тестовая оплата</h1><p>Деньги не списываются. ЮKassa и рабочие VPN-серверы не используются.</p>
<p>{escape(order["title"])}, {order["months"]} мес: {order["amount_rub"]} ₽</p>
<form method="post" action="/demo/pay/{key}/paid"><button>Имитировать успешную оплату</button></form>
<form method="post" action="/demo/pay/{key}/canceled"><button>Имитировать отмену</button></form>
<a href="{ORIGIN}/checkout/{key}">Вернуться без оплаты</a></html>'''


@app.post("/demo/pay/{key}/{outcome}")
async def complete(key: str, outcome: str):
    order = await s.store.order(key)
    if not order or outcome not in {"paid", "canceled"}:
        raise HTTPException(404)
    raw = s.provider.data[order["provider_id"]]
    raw.update(
        status="succeeded" if outcome == "paid" else "canceled", paid=outcome == "paid"
    )
    if outcome == "paid":
        raw["captured_at"] = s.clock().isoformat()
    return RedirectResponse(ORIGIN + "/checkout/" + key, status_code=303)


@app.get("/api/users/{key}")
async def panel_user(key: int):
    user = await s.panel.get_user(key)
    if not user:
        raise HTTPException(404)
    return {
        "response": {
            "id": user.id,
            "status": user.status,
            "expireAt": user.expire_at,
            "telegramId": None,
            "hwidDeviceLimit": user.device_limit,
            "trafficLimitBytes": user.traffic_limit_bytes,
            "trafficLimitStrategy": user.traffic_limit_strategy,
            "subscriptionUrl": BANK + "/demo/subscription/" + str(key),
            "userTraffic": {"usedTrafficBytes": 0},
            "createdAt": s.clock(),
        }
    }


@app.get("/api/nodes")
async def nodes():
    return {"response": []}


@app.get("/api/hwid/devices/{key}")
async def devices(key: int):
    return {"response": {"devices": []}}


@app.get("/api/bandwidth-stats/users/{key}")
async def usage(key: int):
    return {"response": {"categories": [], "sparklineData": [], "series": []}}


@app.get("/demo/subscription/{key}", response_class=HTMLResponse)
async def fake_subscription(key: int):
    return (
        "<h1>Тестовая подписка</h1><p>Это локальная демонстрация, VPN не выдается.</p>"
    )
