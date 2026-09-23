"""DI middleware (release 3.0 Foundation).

Registered as an OUTER middleware on ``dp.update`` so every nested event
(message, callback_query, pre_checkout_query, ...) gets the ports in its
handler data. Handlers ask for them by name:

    async def on_plan(cb: CallbackQuery, callback_data: Plan, checkout: CheckoutService): ...

Injected keys (see app.container.Container): container, remna, payments,
stars, provisioning, status_service, devices, checkout, promo, notifier,
maintenance. 2.x handlers ignore them (aiogram passes only declared params).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware


class DIMiddleware(BaseMiddleware):
    def __init__(self, container: Any):
        self.container = container

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        data.update(self.container.handler_data())
        return await handler(event, data)
