"""Keyboards that money services attach to their messages (release 3.0, A).

Services must not import aiogram, yet a paid-user message needs a
«Подключиться» button and an admin alert needs «Вернуть / Отклонить». So the
services ask a ``MoneyUi`` for an opaque markup object and hand it to the
Notifier. The Telegram implementation is app.bot.views.money.TelegramMoneyUi.

``default_ui()`` is the composition fallback while the container has no
``ui`` slot (orchestrator request A-3): it imports the bot view lazily, so no
module under app.services imports aiogram at import time.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol


class MoneyUi(Protocol):
    def paid(self, payment_id: int, *, refund_button: bool) -> Any: ...
    def gift_paid(self) -> Any: ...
    def review_admin(self, payment_id: int) -> Any: ...
    def refund_admin(self, request_id: int) -> Any: ...
    def autopay_notice(self) -> Any: ...
    def renew(self) -> Any: ...
    def support(self) -> Any: ...


class NullUi:
    """No buttons (tests, or a process without a bot view)."""

    def paid(self, payment_id: int, *, refund_button: bool) -> Any:
        return None

    def gift_paid(self) -> Any:
        return None

    def review_admin(self, payment_id: int) -> Any:
        return None

    def refund_admin(self, request_id: int) -> Any:
        return None

    def autopay_notice(self) -> Any:
        return None

    def renew(self) -> Any:
        return None

    def support(self) -> Any:
        return None


_ui: Optional[MoneyUi] = None


def set_ui(ui: Optional[MoneyUi]) -> None:
    global _ui
    _ui = ui


def default_ui() -> MoneyUi:
    if _ui is not None:
        return _ui
    try:
        from app.bot.views.money import TelegramMoneyUi
    except ImportError:  # pragma: no cover - bot layer always ships with the app
        return NullUi()
    return TelegramMoneyUi()
