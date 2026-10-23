"""Telegram faults must not replay business operations or leave users silent."""
from unittest.mock import AsyncMock

import pytest
from aiogram import Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, AnswerPreCheckoutQuery, GetUpdates, SendMessage
from aiogram.types import ErrorEvent, PreCheckoutQuery, Update

from app.bot.middlewares.errors import ErrorsMiddleware
from app.domain.texts.common import GENERIC_ERROR, GENERIC_ERROR_ALERT
from app.infra.telegram import RetryAfterMiddleware, create_bot
from app.middlewares.tg_errors import global_errors_handler
from tests.fakes.bot import BOT_TOKEN, RecordingSession, callback_update, message_update, user


def bot_session():
    session = RecordingSession()
    return create_bot(BOT_TOKEN, session=session), session


async def test_rate_limit_retries_request_without_replaying_handler(monkeypatch):
    bot, session = bot_session()
    sleep = AsyncMock()
    monkeypatch.setattr("app.infra.telegram.asyncio.sleep", sleep)
    attempts, operations = [], []

    async def reject_once(next_request, bot, method):
        attempts.append(method)
        if len(attempts) == 1:
            raise TelegramRetryAfter(method=method, message="Flood", retry_after=1)
        return await next_request(bot, method)

    bot.session.middleware(reject_once)
    dp = Dispatcher()

    async def purchase(message):
        operations.append("payment already committed")
        await message.answer("Done")

    dp.message.register(purchase)
    await dp.feed_update(bot, message_update(user(), "buy"))
    assert operations == ["payment already committed"]
    assert len(attempts) == 2 and attempts[0] is attempts[1]
    assert session.texts() == ["Done"]
    sleep.assert_awaited_once_with(1)


async def test_retry_exhaustion_is_bounded(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("app.infra.telegram.asyncio.sleep", sleep)
    request = SendMessage(chat_id=1, text="test")
    fail = AsyncMock(side_effect=TelegramRetryAfter(method=request, message="Flood", retry_after=1))
    with pytest.raises(TelegramRetryAfter):
        await RetryAfterMiddleware()(fail, None, request)
    assert fail.await_count == 3 and sleep.await_count == 2


@pytest.mark.parametrize("method,wait", [
    (AnswerCallbackQuery(callback_query_id="x"), 4),
    (AnswerPreCheckoutQuery(pre_checkout_query_id="x", ok=True), 4),
    (SendMessage(chat_id=1, text="test"), 31),
    (GetUpdates(), 1),
])
async def test_long_waits_and_polling_are_not_retried(monkeypatch, method, wait):
    sleep = AsyncMock()
    monkeypatch.setattr("app.infra.telegram.asyncio.sleep", sleep)
    fail = AsyncMock(side_effect=TelegramRetryAfter(method=method, message="Flood", retry_after=wait))
    with pytest.raises(TelegramRetryAfter):
        await RetryAfterMiddleware()(fail, None, method)
    assert fail.await_count == 1
    sleep.assert_not_awaited()


async def test_network_error_is_not_retried():
    request = SendMessage(chat_id=1, text="test")
    fail = AsyncMock(side_effect=TelegramNetworkError(method=request, message="timeout"))
    with pytest.raises(TelegramNetworkError):
        await RetryAfterMiddleware()(fail, None, request)
    assert fail.await_count == 1


@pytest.mark.parametrize("fault", ["can't parse entities", "query is too old", "rate_limit"])
async def test_telegram_errors_get_generic_user_feedback(fault):
    bot, session = bot_session()
    cq = callback_update(bot, user(), "test").callback_query.as_(bot)

    async def fail(event, data):
        method = SendMessage(chat_id=user().id, text="secret")
        if fault == "rate_limit":
            raise TelegramRetryAfter(method=method, message="Flood", retry_after=31)
        raise TelegramBadRequest(method=method, message=fault)

    await ErrorsMiddleware()(fail, cq, {})
    assert session.texts() == [GENERIC_ERROR_ALERT]


async def test_expired_alert_falls_back_to_message_in_original_chat():
    bot, session = bot_session()
    session.fail["AnswerCallbackQuery"] = TelegramBadRequest(
        method=AnswerCallbackQuery(callback_query_id="x"), message="query is too old")
    cq = callback_update(bot, user(), "test").callback_query.as_(bot)

    async def fail(event, data):
        raise RuntimeError("secret")

    await ErrorsMiddleware()(fail, cq, {})
    fallback = session.calls_of("SendMessage")
    assert len(fallback) == 1 and fallback[0].text == GENERIC_ERROR
    assert fallback[0].chat_id == cq.message.chat.id


async def test_failing_fallback_does_not_raise_or_replay():
    bot, session = bot_session()
    session.fail["AnswerCallbackQuery"] = TelegramBadRequest(
        method=AnswerCallbackQuery(callback_query_id="x"), message="query is too old")
    session.fail["SendMessage"] = TelegramForbiddenError(
        method=SendMessage(chat_id=1, text="x"), message="blocked")
    cq = callback_update(bot, user(), "test").callback_query.as_(bot)
    await ErrorsMiddleware._tell_user(cq)
    assert len(session.calls) == 2


async def test_precheckout_failure_rejects_payment():
    bot, session = bot_session()
    q = PreCheckoutQuery(id="x", from_user=user(), currency="XTR", total_amount=10,
                         invoice_payload="test").as_(bot)

    async def fail(event, data):
        raise RuntimeError("secret")

    await ErrorsMiddleware()(fail, q, {})
    answer = session.calls_of("AnswerPreCheckoutQuery")[0].params
    assert answer["ok"] is False and answer["error_message"] == GENERIC_ERROR_ALERT


async def test_global_handler_reports_errors_outside_r3_middleware():
    bot, session = bot_session()
    dp = Dispatcher()
    dp.errors.register(global_errors_handler)

    async def fail(message):
        raise TelegramBadRequest(method=SendMessage(chat_id=1, text="x"), message="can't parse entities")

    dp.message.register(fail)
    await dp.feed_update(bot, message_update(user(), "test"))
    assert session.texts() == [GENERIC_ERROR]


async def test_unchanged_message_is_ignored():
    bot, session = bot_session()
    update = message_update(user(), "test").as_(bot)
    exc = TelegramBadRequest(method=SendMessage(chat_id=1, text="x"), message="message is not modified")
    assert await global_errors_handler(ErrorEvent(update=update, exception=exc))
    assert session.calls == []


@pytest.mark.parametrize("reason,inactive", [
    ("Forbidden: bot was blocked by the user", True),
    ("Forbidden: user is deactivated", True),
    ("Forbidden: not enough rights to send text messages to the chat", False),
])
async def test_only_blocked_or_deactivated_users_are_marked_inactive(monkeypatch, reason, inactive):
    bot, session = bot_session()
    mark = AsyncMock()
    monkeypatch.setattr("app.middlewares.tg_errors._mark_inactive", mark)
    update = message_update(user(), "test").as_(bot)
    exc = TelegramForbiddenError(method=SendMessage(chat_id=1, text="x"), message=reason)
    await global_errors_handler(ErrorEvent(update=update, exception=exc))
    assert mark.await_count == int(inactive)
    assert session.calls == []


async def test_global_precheckout_fallback():
    bot, session = bot_session()
    q = PreCheckoutQuery(id="x", from_user=user(), currency="XTR", total_amount=10, invoice_payload="test")
    dp = Dispatcher()
    dp.errors.register(global_errors_handler)

    async def fail(query):
        raise RuntimeError("secret")

    dp.pre_checkout_query.register(fail)
    await dp.feed_update(bot, Update(update_id=1, pre_checkout_query=q))
    assert session.calls_of("AnswerPreCheckoutQuery")[0].params["ok"] is False
