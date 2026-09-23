"""PaymentGateway over the async YooKassa client. Owner: stream A (Money).

Pure provider adapter: no database, no prices. The amount comes from the
PaymentIntent, which CheckoutService builds from a server-side Quote.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional

from app.domain.models import PaymentIntent, PaymentStatus
from app.infra.yookassa.client import YooKassaClient, YooKassaError
from app.logger import logger

# Same set 2.x sent. Omitted when the card must be saved (autopay): then
# YooKassa decides which methods support saving.
PAYMENT_METHOD_TYPES = ["bank_card", "sbp", "yoo_money"]
DESCRIPTION_MAX = 128

_STATUS = {
    "pending": PaymentStatus.PENDING,
    "waiting_for_capture": PaymentStatus.PENDING,
    "succeeded": PaymentStatus.SUCCEEDED,
    "canceled": PaymentStatus.CANCELED,
}


def _money(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def card_fingerprint(card: Optional[Mapping[str, Any]]) -> Optional[str]:
    """first6-last4-MM/YYYY, the same format as the 2.x stop-list."""
    if not card:
        return None
    parts = (card.get("first6"), card.get("last4"), card.get("expiry_month"), card.get("expiry_year"))
    if not all(parts):
        return None
    return f"{parts[0]}-{parts[1]}-{parts[2]}/{parts[3]}"


def normalize_payment(raw: Mapping[str, Any]) -> dict:
    """Provider JSON -> the dict every caller reads (2.x check_payment_status shape plus
    payment_method and card_fingerprint)."""
    amount = raw.get("amount") or {}
    refunded = raw.get("refunded_amount") or {}
    pm = raw.get("payment_method") or {}
    card = pm.get("card") if isinstance(pm, dict) else None
    method = None
    if isinstance(pm, dict) and pm:
        method = {
            "id": pm.get("id"),
            "type": pm.get("type"),
            "saved": bool(pm.get("saved")),
            "title": pm.get("title") or (f"Карта *{card.get('last4')}" if isinstance(card, dict) and card.get("last4") else None),
        }
    return {
        "id": raw.get("id"),
        "status": raw.get("status"),
        "paid": bool(raw.get("paid")),
        "amount": _money(amount.get("value")),
        "currency": amount.get("currency") or "RUB",
        "description": raw.get("description"),
        "metadata": dict(raw.get("metadata") or {}),
        "refunded_amount": _money(refunded.get("value")),
        "payment_method": method,
        "card_fingerprint": card_fingerprint(card if isinstance(card, dict) else None),
        "created_at": raw.get("created_at"),
        "captured_at": raw.get("captured_at"),
        "cancellation_reason": (raw.get("cancellation_details") or {}).get("reason"),
    }


def _settings():
    from app.config import settings

    return settings


class YooKassaGateway:
    """app.services.ports.PaymentGateway for YooKassa."""

    def __init__(self, client_factory: Optional[Callable[[], YooKassaClient]] = None, settings: Any = None):
        self._factory = client_factory
        self._settings = settings
        self._client: Optional[YooKassaClient] = None

    @property
    def settings(self):
        return self._settings if self._settings is not None else _settings()

    def client(self) -> YooKassaClient:
        if self._client is None:
            if self._factory is not None:
                self._client = self._factory()
            else:
                s = self.settings
                self._client = YooKassaClient(s.YOOKASSA_SHOP_ID or "", s.YOOKASSA_API_KEY or "")
        return self._client

    async def create_payment(
        self,
        intent: PaymentIntent,
        *,
        description: str,
        idempotence_key: str,
        save_payment_method: bool = False,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> PaymentIntent:
        if intent.amount_rub <= 0:
            raise ValueError("create_payment: amount must come from a sellable Quote")
        return_url = self.settings.YOOKASSA_RETURN_URL
        if not return_url:
            raise ValueError("YOOKASSA_RETURN_URL is not configured")
        body: dict[str, Any] = {
            "amount": {"value": f"{int(intent.amount_rub)}.00", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": str(return_url)},
            "description": description[:DESCRIPTION_MAX],
            "metadata": {k: str(v) for k, v in (metadata or {}).items() if v is not None},
        }
        if save_payment_method:
            body["save_payment_method"] = True
        else:
            body["payment_method_types"] = list(PAYMENT_METHOD_TYPES)
        raw = await self.client().create_payment(body, idempotence_key)
        ext = raw.get("id")
        if not ext:
            raise YooKassaError("create_payment: response without id", retryable=True)
        url = (raw.get("confirmation") or {}).get("confirmation_url")
        return replace(
            intent,
            external_id=str(ext),
            confirmation_url=url,
            status=_STATUS.get(raw.get("status"), PaymentStatus.PENDING),
        )

    async def get_payment(self, external_id: str) -> Optional[dict]:
        try:
            raw = await self.client().get_payment(external_id)
        except YooKassaError as e:
            if e.status in (400, 404):
                return {"error": "not_found"}
            logger.warning(f"yookassa get_payment {external_id}: {e}")
            return None
        except ValueError as e:  # not configured
            logger.error(f"yookassa get_payment: {e}")
            return None
        if raw is None:
            return {"error": "not_found"}
        return normalize_payment(raw)

    async def charge_saved_method(
        self,
        intent: PaymentIntent,
        *,
        payment_method_id: str,
        description: str,
        idempotence_key: str,
    ) -> PaymentIntent:
        if intent.amount_rub <= 0:
            raise ValueError("charge_saved_method: amount must come from a sellable Quote")
        body = {
            "amount": {"value": f"{int(intent.amount_rub)}.00", "currency": "RUB"},
            "capture": True,
            "payment_method_id": payment_method_id,
            "description": description[:DESCRIPTION_MAX],
            "metadata": {
                "tg_user_id": str(intent.telegram_id or ""),
                "plan_code": intent.plan_code,
                "period_months": str(intent.months),
                "kind": intent.kind.value,
            },
        }
        raw = await self.client().create_payment(body, idempotence_key)
        ext = raw.get("id")
        if not ext:
            raise YooKassaError("charge_saved_method: response without id", retryable=True)
        return replace(intent, external_id=str(ext), status=_STATUS.get(raw.get("status"), PaymentStatus.PENDING))

    async def refund(
        self,
        external_id: str,
        *,
        amount_rub: Optional[Decimal] = None,
        idempotence_key: str,
        reason: str = "",
    ) -> Optional[dict]:
        try:
            if amount_rub is None:
                view = await self.get_payment(external_id)
                if not view or view.get("error"):
                    return None
                amount = Decimal(str(view["amount"])) - Decimal(str(view.get("refunded_amount") or 0))
            else:
                amount = Decimal(str(amount_rub))
            if amount <= 0:
                return None
            body: dict[str, Any] = {
                "payment_id": external_id,
                "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            }
            if reason:
                body["description"] = reason[:250]
            raw = await self.client().create_refund(body, idempotence_key)
        except (YooKassaError, ValueError) as e:
            logger.error(f"yookassa refund for {external_id} failed: {e}")
            return None
        return {
            "id": raw.get("id"),
            "status": raw.get("status"),
            "payment_id": raw.get("payment_id") or external_id,
            "amount": _money((raw.get("amount") or {}).get("value")),
        }

    async def get_refund(self, refund_id: str) -> Optional[dict]:
        """Not in the port: used by the refund webhook. None on API error,
        {"error": "not_found"} for an unknown id."""
        try:
            raw = await self.client().get_refund(refund_id)
        except YooKassaError as e:
            logger.error(f"yookassa get_refund {refund_id}: {e}")
            return None
        except ValueError as e:
            logger.error(f"yookassa get_refund: {e}")
            return None
        if raw is None:
            return {"error": "not_found"}
        amount = raw.get("amount") or {}
        return {
            "id": raw.get("id"),
            "status": raw.get("status"),
            "payment_id": raw.get("payment_id"),
            "amount": _money(amount.get("value")),
            "currency": amount.get("currency"),
        }


_default: Optional[YooKassaGateway] = None


def default_gateway() -> YooKassaGateway:
    """Process-wide gateway for 2.x call sites that have no container at hand."""
    global _default
    if _default is None:
        _default = YooKassaGateway()
    return _default
