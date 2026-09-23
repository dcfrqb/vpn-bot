"""Async YooKassa client and gateway over httpx.MockTransport (no network, no SDK)."""
import json
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from app.domain.models import PaymentIntent, PaymentKind, PaymentStatus
from app.infra.yookassa import YooKassaClient, YooKassaError, YooKassaGateway


class _Settings:
    YOOKASSA_SHOP_ID = "shop"
    YOOKASSA_API_KEY = "secret"
    YOOKASSA_RETURN_URL = "https://t.me/test_bot"


def _client(handler, **kw):
    return YooKassaClient("shop", "secret", transport=httpx.MockTransport(handler), sleep=AsyncMock(), **kw)


def _gw(handler):
    return YooKassaGateway(client_factory=lambda: _client(handler), settings=_Settings())


PAYMENT = {
    "id": "2f8a-1", "status": "pending", "paid": False,
    "amount": {"value": "329.00", "currency": "RUB"},
    "confirmation": {"type": "redirect", "confirmation_url": "https://yoomoney.ru/checkout/x"},
    "metadata": {"tg_user_id": "5"},
}


async def test_post_sends_idempotence_key_and_basic_auth():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=PAYMENT)

    data = await _client(handler).create_payment({"amount": {}}, "key-1")
    assert data["id"] == "2f8a-1"
    assert seen[0].headers["Idempotence-Key"] == "key-1"
    assert seen[0].headers["Authorization"].startswith("Basic ")


async def test_5xx_and_network_errors_retry_with_the_same_key():
    calls = []

    def handler(req):
        calls.append(req.headers["Idempotence-Key"])
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        if len(calls) == 2:
            return httpx.Response(500)
        return httpx.Response(200, json=PAYMENT)

    assert (await _client(handler).create_payment({}, "same"))["id"] == "2f8a-1"
    assert calls == ["same", "same", "same"]


async def test_202_processing_is_repeated():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(202, json={"retry_after": 100}) if len(calls) == 1 else httpx.Response(200, json=PAYMENT)

    assert (await _client(handler).create_payment({}, "k"))["id"] == "2f8a-1"
    assert len(calls) == 2


async def test_errors_are_classified():
    with pytest.raises(YooKassaError) as e:
        await _client(lambda r: httpx.Response(400, json={"code": "invalid_request", "description": "bad"})) \
            .create_payment({}, "k")
    assert e.value.status == 400 and not e.value.retryable and e.value.code == "invalid_request"
    with pytest.raises(YooKassaError) as e:
        await _client(lambda r: httpx.Response(503)).create_payment({}, "k")
    assert e.value.retryable
    assert await _client(lambda r: httpx.Response(404)).get_payment("nope") is None


async def test_gateway_create_payment_body_and_intent():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=PAYMENT)

    gw = _gw(handler)
    intent = PaymentIntent(plan_code="lite", months=3, amount_rub=329, telegram_id=5)
    out = await gw.create_payment(intent, description="CRS VPN Lite", idempotence_key="k",
                                  metadata={"tg_user_id": 5, "plan_code": "lite", "x": None})
    body = bodies[0]
    assert body["amount"] == {"value": "329.00", "currency": "RUB"} and body["capture"] is True
    assert body["metadata"] == {"tg_user_id": "5", "plan_code": "lite"}
    assert body["payment_method_types"] and "save_payment_method" not in body
    assert out.external_id == "2f8a-1" and out.confirmation_url.startswith("https://") and out.status is PaymentStatus.PENDING

    await gw.create_payment(intent, description="d", idempotence_key="k2", save_payment_method=True)
    assert bodies[1]["save_payment_method"] is True and "payment_method_types" not in bodies[1]
    with pytest.raises(ValueError):
        await gw.create_payment(PaymentIntent(plan_code="x", months=1, amount_rub=0), description="d",
                                idempotence_key="k3")


async def test_gateway_get_payment_normalizes_card_and_method():
    raw = dict(PAYMENT, status="succeeded", paid=True, refunded_amount={"value": "10.00", "currency": "RUB"},
               payment_method={"id": "pm-1", "type": "bank_card", "saved": True,
                               "card": {"first6": "220000", "last4": "1234", "expiry_month": "12",
                                        "expiry_year": "2030"}})
    view = await _gw(lambda r: httpx.Response(200, json=raw)).get_payment("2f8a-1")
    assert view["status"] == "succeeded" and view["amount"] == 329.0 and view["refunded_amount"] == 10.0
    assert view["payment_method"] == {"id": "pm-1", "type": "bank_card", "saved": True, "title": "Карта *1234"}
    assert view["card_fingerprint"] == "220000-1234-12/2030"
    assert (await _gw(lambda r: httpx.Response(404)).get_payment("x")) == {"error": "not_found"}
    assert (await _gw(lambda r: httpx.Response(500)).get_payment("x")) is None


async def test_gateway_full_refund_uses_remaining_amount():
    bodies = []

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json=dict(PAYMENT, status="succeeded",
                                                 refunded_amount={"value": "29.00", "currency": "RUB"}))
        bodies.append((json.loads(req.content), req.headers["Idempotence-Key"]))
        return httpx.Response(200, json={"id": "rf-1", "status": "succeeded", "payment_id": "2f8a-1",
                                         "amount": {"value": "300.00", "currency": "RUB"}})

    res = await _gw(handler).refund("2f8a-1", idempotence_key="rr:1", reason="24h")
    assert res == {"id": "rf-1", "status": "succeeded", "payment_id": "2f8a-1", "amount": 300.0}
    assert bodies[0] == ({"payment_id": "2f8a-1", "amount": {"value": "300.00", "currency": "RUB"},
                          "description": "24h"}, "rr:1")
    partial = await _gw(handler).refund("2f8a-1", amount_rub=Decimal("5"), idempotence_key="rr:2")
    assert partial["id"] == "rf-1" and bodies[1][0]["amount"]["value"] == "5.00"
    assert await _gw(lambda r: httpx.Response(500)).refund("x", idempotence_key="k") is None


async def test_gateway_charge_saved_method():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=dict(PAYMENT, status="succeeded"))

    intent = PaymentIntent(plan_code="pro", months=1, amount_rub=449, kind=PaymentKind.AUTORENEW, telegram_id=5)
    out = await _gw(handler).charge_saved_method(intent, payment_method_id="pm-1", description="d",
                                                 idempotence_key="autopay:1")
    assert bodies[0]["payment_method_id"] == "pm-1" and "confirmation" not in bodies[0]
    assert out.status is PaymentStatus.SUCCEEDED


async def test_gateway_get_refund():
    gw = _gw(lambda r: httpx.Response(200, json={"id": "rf", "status": "succeeded", "payment_id": "p",
                                                 "amount": {"value": "1.00", "currency": "RUB"}}))
    assert await gw.get_refund("rf") == {"id": "rf", "status": "succeeded", "payment_id": "p", "amount": 1.0,
                                         "currency": "RUB"}
    assert await _gw(lambda r: httpx.Response(404)).get_refund("x") == {"error": "not_found"}


def test_money_path_does_not_import_the_sync_sdk():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "app"
    live = [root / "infra" / "yookassa" / "client.py", root / "infra" / "yookassa" / "gateway.py",
            root / "services" / "fulfillment.py", root / "services" / "checkout.py",
            root / "services" / "payments" / "webhook.py", root / "services" / "payments" / "refunds.py",
            root / "services" / "payments" / "refund_requests.py", root / "services" / "autopay.py"]
    for f in live:
        text = f.read_text()
        assert "from yookassa" not in text and "import yookassa" not in text, f


async def test_stars_gateway_sends_xtr_invoice_and_refunds():
    from app.infra.telegram_stars import TelegramStarsGateway
    from tests.fakes.bot import make_bot

    bot, session = make_bot()
    gw = TelegramStarsGateway(bot)
    intent = PaymentIntent(plan_code="lite", months=1, amount_rub=1, stars=90)
    await gw.send_invoice(5, intent, title="CRS VPN Lite", description="Lite, 1 месяц", payload="p:7")
    call = session.calls_of("SendInvoice")[0]
    assert call.params["currency"] == "XTR" and call.params["payload"] == "p:7"
    assert call.params["prices"][0]["amount"] == 90
    assert await gw.refund(5, "charge-1") is True
    assert session.calls_of("RefundStarPayment")[0].params["telegram_payment_charge_id"] == "charge-1"
    with pytest.raises(ValueError):
        await gw.send_invoice(5, PaymentIntent(plan_code="lite", months=1, amount_rub=1), title="t",
                              description="d", payload="p:1")
