"""Telegram Stars (XTR) adapter. Owner stream: A (Money).

Implements app.services.ports.StarsGateway over the Bot API:
- ``send_invoice``: sendInvoice with currency XTR and one LabeledPrice
  (provider_token is empty for digital goods paid in Stars);
- ``refund``: refundStarPayment.

``bot`` may be given later: when it is None the gateway takes the bot of the
process container (the Foundation container builds ports without a bot).
"""
from __future__ import annotations

from typing import Any

from app.domain.models import PaymentIntent
from app.logger import logger

XTR = "XTR"
PAYLOAD_MAX = 128


class TelegramStarsGateway:
    def __init__(self, bot: Any = None):
        self._bot = bot

    @property
    def bot(self) -> Any:
        if self._bot is not None:
            return self._bot
        from app.container import get_container

        return get_container().bot

    async def send_invoice(self, chat_id: int, intent: PaymentIntent, *, title: str, description: str,
                           payload: str) -> None:
        from aiogram.types import LabeledPrice

        stars = int(intent.stars or 0)
        if stars <= 0:
            raise ValueError("send_invoice: no Stars price in the intent")
        if len(payload.encode()) > PAYLOAD_MAX:
            raise ValueError("send_invoice: payload longer than 128 bytes")
        await self.bot.send_invoice(
            chat_id=int(chat_id),
            title=title[:32],
            description=description[:255],
            payload=payload,
            currency=XTR,
            prices=[LabeledPrice(label=title[:32], amount=stars)],
            provider_token="",
        )

    async def refund(self, telegram_id: int, telegram_charge_id: str) -> bool:
        try:
            return bool(await self.bot.refund_star_payment(
                user_id=int(telegram_id), telegram_payment_charge_id=str(telegram_charge_id),
            ))
        except Exception as e:  # noqa: BLE001 - the caller records the failure
            if "ALREADY_REFUNDED" in str(e).upper():
                # A retry after a lost answer: the stars are already back (m-7).
                logger.warning(f"stars refund for user={telegram_id}: already refunded, counted as done")
                return True
            logger.error(f"stars refund failed for user={telegram_id}: {type(e).__name__}: {str(e)[:200]}")
            return False


__all__ = ["TelegramStarsGateway", "XTR"]

