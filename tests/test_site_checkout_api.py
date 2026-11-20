"""Internal checkout guard, shape and sanitized failures."""

from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import site_checkout
from app.api.internal_site import InternalApiError, internal_api_error_handler
from app.config import settings
from app.services.site_commerce import SiteCommerceError
from app.services.site_store import SiteBusy


def test_guard_and_error_redaction(monkeypatch):
    app = FastAPI()
    app.include_router(site_checkout.router)
    app.add_exception_handler(InternalApiError, internal_api_error_handler)
    app.add_exception_handler(SiteCommerceError, site_checkout.error_handler)
    app.add_exception_handler(SiteBusy, site_checkout.error_handler)
    monkeypatch.setattr(settings, "BOT_INTERNAL_TOKEN", "test-internal")
    quote = AsyncMock(side_effect=RuntimeError("sensitive SQL/payment URL"))
    monkeypatch.setattr(
        site_checkout, "commerce", lambda: type("Service", (), {"quote": quote})()
    )
    with TestClient(app) as client:
        body = {"account_id": str(uuid4()), "plan_code": "lite", "months": 1}
        assert (
            client.post("/internal/site/checkout/quote", json=body).status_code == 403
        )
        result = client.post(
            "/internal/site/checkout/quote",
            json=body,
            headers={"X-Internal-Token": "test-internal"},
        )
        assert result.status_code == 503 and result.json() == {
            "error": "checkout_unavailable"
        }
        assert "sensitive" not in result.text
        result = client.post(
            "/internal/site/checkout/orders",
            json={
                **body,
                "order_id": str(uuid4()),
                "expected_amount_rub": 129,
                "amount": 1,
            },
            headers={"X-Internal-Token": "test-internal"},
        )
        assert result.status_code == 422
