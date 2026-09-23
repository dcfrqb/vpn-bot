"""3.0 Foundation: DI, errors and maintenance middlewares (unit level)."""
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery

from app.bot.middlewares.di import DIMiddleware
from app.bot.middlewares.errors import ErrorsMiddleware
from app.bot.middlewares.maintenance import MaintenanceMiddleware
from app.container import HANDLER_KEYS, build_container
from app.domain.models import AdminTopic
from app.domain.texts.common import GENERIC_ERROR, GENERIC_ERROR_ALERT
from app.domain.texts.notify import MAINTENANCE_SCREEN
from tests.fakes.bot import callback_update, make_bot, message_update, user
from tests.fakes.notifier import RecordingNotifier


async def test_di_injects_every_port():
    bot, _ = make_bot()
    c = build_container(bot)
    data = {}

    async def handler(event, d):
        return d

    out = await DIMiddleware(c)(handler, object(), data)
    assert out["container"] is c
    assert set(HANDLER_KEYS.values()) <= set(out)
    assert out["status_service"] is c.status and out["notifier"] is c.notifier


async def test_errors_middleware_generic_text_for_callback_and_message():
    bot, s = make_bot()
    notifier = RecordingNotifier()

    async def boom(event, data):
        raise ValueError("DB password=hunter2")

    cq = callback_update(bot, user(), "x").callback_query.as_(bot)
    assert await ErrorsMiddleware()(boom, cq, {"notifier": notifier}) is None
    msg = message_update(user(), "hi").message.as_(bot)
    await ErrorsMiddleware()(boom, msg, {"notifier": notifier})
    texts = [c.params.get("text") for c in s.calls]
    assert texts == [GENERIC_ERROR_ALERT, GENERIC_ERROR]
    assert "hunter2" not in str(texts)
    # admins get the details once (dedup per handler+type)
    assert len(notifier.to_admins(AdminTopic.ERRORS)) == 1


async def test_errors_middleware_reraises_telegram_errors():
    async def tg(event, data):
        raise TelegramBadRequest(method=AnswerCallbackQuery(callback_query_id="1"), message="bad")

    with pytest.raises(TelegramBadRequest):
        await ErrorsMiddleware()(tg, object(), {})


class Guard:
    def __init__(self, active):
        self.active = active
        self.calls = 0

    async def is_active(self):
        self.calls += 1
        if isinstance(self.active, Exception):
            raise self.active
        return self.active


async def test_maintenance_noop_when_off_and_cached():
    g = Guard(False)
    mw = MaintenanceMiddleware(g, admin_ids=lambda: [])
    seen = []

    async def handler(event, data):
        seen.append(1)

    bot, _ = make_bot()
    msg = message_update(user(), "hi").message.as_(bot)
    await mw(handler, msg, {})
    await mw(handler, msg, {})
    assert seen == [1, 1] and g.calls == 1  # cached


async def test_maintenance_blocks_users_admin_passes_and_fails_open():
    bot, s = make_bot()
    mw = MaintenanceMiddleware(Guard(True), admin_ids=lambda: [42])
    seen = []

    async def handler(event, data):
        seen.append(event.from_user.id)

    await mw(handler, message_update(user(1), "/trial").message.as_(bot), {})
    await mw(handler, message_update(user(42), "/trial").message.as_(bot), {})
    assert seen == [42]
    assert s.calls_of("SendMessage")[0].text == MAINTENANCE_SCREEN

    broken = MaintenanceMiddleware(Guard(RuntimeError("redis")), admin_ids=lambda: [])
    await broken(handler, message_update(user(2), "/trial").message.as_(bot), {})
    assert seen == [42, 2]


async def test_blocklist_lets_successful_payment_through_and_drops_the_rest():
    """Security m-6: Telegram already took the stars, the payment must be recorded."""
    from datetime import datetime

    from aiogram.types import Chat, Message, SuccessfulPayment, User

    from app.middlewares import blocklist as bl

    uid = 900000321
    bl._runtime_blocked.add(uid)
    try:
        seen = []

        async def handler(event, data):
            seen.append(event)

        common = dict(message_id=1, date=datetime(2026, 9, 23), chat=Chat(id=uid, type="private"),
                      from_user=User(id=uid, is_bot=False, first_name="x"))
        paid = Message(**common, successful_payment=SuccessfulPayment(
            currency="XTR", total_amount=90, invoice_payload="p:1",
            telegram_payment_charge_id="c", provider_payment_charge_id="p"))
        await bl.BlocklistMiddleware()(handler, paid, {})
        assert seen == [paid]
    finally:
        bl._runtime_blocked.discard(uid)
