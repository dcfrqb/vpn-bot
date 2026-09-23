"""Fake Telegram side for tests (release 3.0 Foundation).

``RecordingSession`` is an aiogram BaseSession: nothing goes to the network,
every Bot API call is recorded as a ``Call`` and answered with a plausible
result (Message for send/edit, True for the rest, a bot User for getMe).
Ideas from debug/harness/hlib.py (capture instead of delivery).

    bot, session = make_bot()
    await dp.feed_update(bot, callback_update(user(), "back_to_main"))
    session.calls_of("AnswerCallbackQuery")
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
    User,
)

BOT_ID = 7000000001
BOT_TOKEN = f"{BOT_ID}:AAFakeTokenForTestsOnly0123456789abc"
_ids = itertools.count(int(time.time()) % 100000 * 1000)


def next_id() -> int:
    return next(_ids)


def markup_rows(markup: Any) -> Optional[list[list[dict]]]:
    rows = getattr(markup, "inline_keyboard", None)
    if rows is None:
        return None
    return [[{"text": b.text, "data": b.callback_data, "url": b.url} for b in row] for row in rows]


@dataclass
class Call:
    method: str
    params: dict = field(default_factory=dict)

    @property
    def text(self) -> Optional[str]:
        return self.params.get("text") or self.params.get("caption")

    @property
    def chat_id(self) -> Any:
        return self.params.get("chat_id")

    @property
    def keyboard(self) -> Optional[list[list[dict]]]:
        return markup_rows(self.params.get("reply_markup"))


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[Call] = []
        self.fail: dict[str, Exception] = {}  # method name -> exception to raise

    async def close(self) -> None:
        return None

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        if False:  # pragma: no cover
            yield b""

    def reset(self) -> None:
        self.calls.clear()
        self.fail.clear()

    def calls_of(self, method: str) -> list[Call]:
        return [c for c in self.calls if c.method == method]

    def texts(self) -> list[str]:
        return [c.text for c in self.calls if c.text]

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: Optional[int] = None) -> Any:
        name = type(method).__name__
        params = {k: v for k, v in method.model_dump().items() if v is not None}
        # keep the real markup object (model_dump flattens it)
        if getattr(method, "reply_markup", None) is not None:
            params["reply_markup"] = method.reply_markup
        self.calls.append(Call(name, params))
        if name in self.fail:
            raise self.fail[name]
        returning = str(getattr(method, "__returning__", ""))
        if name == "GetMe":
            return User(id=BOT_ID, is_bot=True, first_name="TestBot", username="test_bot")
        if "Message" in returning and "chat_id" in params:
            markup = params.get("reply_markup")
            return Message.model_validate(
                {
                    "message_id": params.get("message_id") or next_id(),
                    "date": int(time.time()),
                    "chat": {"id": int(params["chat_id"]), "type": "private"},
                    "from": {"id": BOT_ID, "is_bot": True, "first_name": "TestBot"},
                    "text": params.get("text") or "",
                    "reply_markup": markup.model_dump() if isinstance(markup, InlineKeyboardMarkup) else None,
                },
                context={"bot": bot},
            )
        return True


def make_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    return bot, session


def user(uid: int = 900000101, username: Optional[str] = "tester", first_name: str = "Test") -> User:
    return User(id=uid, is_bot=False, first_name=first_name, username=username, language_code="ru")


def bot_message(bot: Bot, chat_id: int, text: str = "screen", markup: Any = None) -> Message:
    return Message.model_validate(
        {
            "message_id": next_id(),
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": BOT_ID, "is_bot": True, "first_name": "TestBot"},
            "text": text,
            "reply_markup": markup.model_dump() if isinstance(markup, InlineKeyboardMarkup) else None,
        },
        context={"bot": bot},
    )


def message_update(u: User, text: str) -> Update:
    entities = None
    if text.startswith("/"):
        cmd = text.split()[0]
        entities = [MessageEntity(type="bot_command", offset=0, length=len(cmd))]
    msg = Message(
        message_id=next_id(),
        date=datetime.now(timezone.utc),
        chat=Chat(id=u.id, type="private"),
        from_user=u,
        text=text,
        entities=entities,
    )
    return Update(update_id=next_id(), message=msg)


def callback_update(bot: Bot, u: User, data: str, message: Optional[Message] = None) -> Update:
    base = message or bot_message(bot, u.id)
    cq = CallbackQuery(id=str(next_id()), from_user=u, chat_instance="test", data=data, message=base)
    return Update(update_id=next_id(), callback_query=cq)
