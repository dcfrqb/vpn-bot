"""Flows: updates go through the REAL aiogram Dispatcher of the bot.

The dispatcher is built once per session (aiogram routers can be attached
to one parent only) by app.bot.dispatcher.build_dispatcher with:
  - a Bot on tests.fakes.bot.RecordingSession (no network, all calls recorded);
  - MemoryStorage; FakeRedis behind app.services.cache.get_redis_client;
  - a Container of fakes (FakeRemnaGateway, FakePaymentGateway, FakeStarsGateway,
    RecordingNotifier) swapped in per test through the DI middleware.

    async def test_x(flow):
        await flow.press("back_to_main")
        assert flow.session.calls_of("AnswerCallbackQuery")

Streams add scenario files here; keep them independent of each other
(``flow`` resets recorded calls, Redis and the container per test).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pytest
from aiogram.fsm.storage.memory import MemoryStorage

from tests.fakes.bot import RecordingSession, callback_update, make_bot, message_update, user
from tests.fakes.devices import FakeDevicesService
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.payments import FakePaymentGateway
from tests.fakes.promo import FakePromoService
from tests.fakes.redis import FakeRedis
from tests.fakes.remnawave import FakeRemnaGateway
from tests.fakes.stars import FakeStarsGateway
from tests.fakes.status import FakeStatusService


@dataclass
class Flow:
    dp: Any
    bot: Any
    session: RecordingSession
    container: Any
    redis: FakeRedis
    notifier: RecordingNotifier
    di: Any
    maintenance_mw: Any
    status: FakeStatusService
    devices_service: FakeDevicesService
    promo: FakePromoService
    user: Any = field(default_factory=user)

    async def send(self, text: str, u: Optional[Any] = None):
        return await self.dp.feed_update(self.bot, message_update(u or self.user, text))

    async def press(self, data: str, u: Optional[Any] = None, message: Any = None):
        return await self.dp.feed_update(self.bot, callback_update(self.bot, u or self.user, data, message))

    def answers(self) -> list:
        return self.session.calls_of("AnswerCallbackQuery")


@pytest.fixture(scope="session")
def _flow_bundle():
    from app.bot.dispatcher import build_dispatcher
    from app.bot.middlewares.di import DIMiddleware
    from app.bot.middlewares.maintenance import MaintenanceMiddleware
    from app.container import build_container, set_container

    bot, session = make_bot()
    container = build_container(bot)
    dp = build_dispatcher(bot, storage=MemoryStorage(), container=container)
    di = next(m for m in dp.update.outer_middleware if isinstance(m, DIMiddleware))
    mmw = next(m for m in dp.message.outer_middleware if isinstance(m, MaintenanceMiddleware))
    yield dp, bot, session, di, mmw
    set_container(None)


@pytest.fixture
def flow(_flow_bundle, monkeypatch):
    from app.container import build_container, set_container

    dp, bot, session, di, mmw = _flow_bundle
    session.reset()
    redis = FakeRedis()
    monkeypatch.setattr("app.services.cache.get_redis_client", lambda: redis)
    notifier = RecordingNotifier()
    status = FakeStatusService()
    devices_service = FakeDevicesService()
    promo = FakePromoService()
    container = build_container(
        bot,
        remna=FakeRemnaGateway(),
        payments=FakePaymentGateway(),
        stars=FakeStarsGateway(),
        notifier=notifier,
        status=status,
        devices=devices_service,
        promo=promo,
    )
    di.container = container
    mmw.guard = container.maintenance
    mmw.reset_cache()
    set_container(container)
    yield Flow(dp=dp, bot=bot, session=session, container=container, redis=redis,
               notifier=notifier, di=di, maintenance_mw=mmw,
               status=status, devices_service=devices_service, promo=promo)
    mmw.reset_cache()
