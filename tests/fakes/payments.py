"""FakePaymentGateway: implements app.services.ports.PaymentGateway in memory.

Payments are created PENDING; tests flip them with ``succeed``/``cancel``.
Idempotence keys are honoured: the same key returns the same payment.
"""
from __future__ import annotations

import itertools
from dataclasses import replace
from decimal import Decimal
from typing import Any, Mapping, Optional

from app.domain.models import PaymentIntent, PaymentStatus


class FakePaymentGateway:
    def __init__(self) -> None:
        self.payments: dict[str, dict] = {}
        self.by_key: dict[str, str] = {}
        self.refunds: list[dict] = []
        self.charges: list[dict] = []
        self.fail_create = False
        self._seq = itertools.count(1)

    async def create_payment(self, intent: PaymentIntent, *, description: str, idempotence_key: str,
                             save_payment_method: bool = False,
                             metadata: Optional[Mapping[str, Any]] = None) -> PaymentIntent:
        if self.fail_create:
            raise RuntimeError("yookassa down")
        if idempotence_key in self.by_key:
            ext = self.by_key[idempotence_key]
        else:
            ext = f"fake-{next(self._seq):06d}"
            self.by_key[idempotence_key] = ext
            self.payments[ext] = {
                "id": ext, "status": "pending", "paid": False, "amount": float(intent.amount_rub),
                "currency": "RUB", "description": description, "metadata": dict(metadata or {}),
                "refunded_amount": 0.0, "save_payment_method": save_payment_method, "payment_method": None,
            }
        return replace(intent, external_id=ext, confirmation_url=f"https://pay.example/{ext}",
                       status=PaymentStatus.PENDING)

    def succeed(self, ext: str, payment_method_id: Optional[str] = None) -> None:
        p = self.payments[ext]
        p.update(status="succeeded", paid=True)
        if payment_method_id:
            p["payment_method"] = {"id": payment_method_id, "saved": True}

    def cancel(self, ext: str) -> None:
        self.payments[ext].update(status="canceled", paid=False)

    async def get_payment(self, external_id: str) -> Optional[dict]:
        p = self.payments.get(external_id)
        return dict(p) if p else {"error": "not_found"}

    async def charge_saved_method(self, intent: PaymentIntent, *, payment_method_id: str, description: str,
                                  idempotence_key: str) -> PaymentIntent:
        self.charges.append({"method": payment_method_id, "key": idempotence_key, "amount": intent.amount_rub})
        return await self.create_payment(intent, description=description, idempotence_key=idempotence_key)

    async def refund(self, external_id: str, *, amount_rub: Optional[Decimal] = None, idempotence_key: str,
                     reason: str = "") -> Optional[dict]:
        p = self.payments.get(external_id)
        if p is None:
            return None
        amount = float(amount_rub) if amount_rub is not None else p["amount"]
        p["refunded_amount"] = p.get("refunded_amount", 0.0) + amount
        r = {"payment_id": external_id, "amount": amount, "key": idempotence_key, "status": "succeeded"}
        self.refunds.append(r)
        return r
