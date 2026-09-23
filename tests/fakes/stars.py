"""FakeStarsGateway: implements app.services.ports.StarsGateway in memory."""
from __future__ import annotations

from app.domain.models import PaymentIntent


class FakeStarsGateway:
    def __init__(self) -> None:
        self.invoices: list[dict] = []
        self.refunds: list[tuple[int, str]] = []

    async def send_invoice(self, chat_id: int, intent: PaymentIntent, *, title: str, description: str,
                           payload: str) -> None:
        self.invoices.append({"chat_id": chat_id, "stars": intent.stars, "title": title, "payload": payload})

    async def refund(self, telegram_id: int, telegram_charge_id: str) -> bool:
        self.refunds.append((telegram_id, telegram_charge_id))
        return True
