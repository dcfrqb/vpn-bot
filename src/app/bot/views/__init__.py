"""View helpers for 3.0 routers (release 3.0 Foundation).

A "view" is (text, InlineKeyboardMarkup | None). Streams put their screens
in modules here (``views/menu.py``, ``views/checkout.py``...) as pure
functions of domain DTOs; routers call ``render``.

    text, kb = views.menu.main(state)          # pure, snapshot-testable
    await render(callback, text, kb)           # edit in place or send

Buttons carry packed callbacks from app.bot.callbacks, never raw strings.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence, Union
from html.parser import HTMLParser

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.logger import logger

ButtonSpec = Union[InlineKeyboardButton, tuple[str, Union[CallbackData, str]]]


def btn(text: str, cb: Union[CallbackData, str]) -> InlineKeyboardButton:
    """Callback button. ``cb`` is a CallbackData (packed here) or an already
    packed string."""
    data = cb.pack() if isinstance(cb, CallbackData) else cb
    return InlineKeyboardButton(text=text, callback_data=data)


def url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url)


def kb(rows: Iterable[Sequence[ButtonSpec]]) -> InlineKeyboardMarkup:
    """kb([[("Назад", Nav(s="main"))], [url_btn("Статья", url)]])"""
    out: list[list[InlineKeyboardButton]] = []
    for row in rows:
        line = []
        for b in row:
            line.append(b if isinstance(b, InlineKeyboardButton) else btn(b[0], b[1]))
        if line:
            out.append(line)
    return InlineKeyboardMarkup(inline_keyboard=out)


_NOT_MODIFIED = "message is not modified"


class _PlainResult(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "blockquote"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "blockquote":
            self.parts.append("\n")


def result_text(text: str) -> str:
    """Telegram alerts accept plain text, at most 200 characters."""
    parser = _PlainResult()
    parser.feed(text)
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


async def show_result(event, text: str, markup=None) -> bool:
    """Short button results are alerts; longer results/commands use the screen.

    Return True for an alert. Expired callbacks fall back to editing the same
    message, without repeating the action that produced the result.
    """
    plain = result_text(text)
    if isinstance(event, CallbackQuery) and len(plain) <= 200:
        try:
            await event.answer(plain, show_alert=True)
            return True
        except TelegramBadRequest:
            await render(event, text, markup, answer_callback=False)
            return False
    await render(event, text, markup)
    return False


async def render(
    event: Union[CallbackQuery, Message],
    text: str,
    markup: Optional[InlineKeyboardMarkup] = None,
    *,
    parse_mode: Optional[str] = "HTML",
    disable_web_page_preview: bool = True,
    answer_callback: bool = True,
) -> Any:
    """Show a screen: edit the callback's message in place, else send new.

    - CallbackQuery: answers the query (unless ``answer_callback=False``),
      then edits text+markup; "not modified" is ignored; a message that
      cannot be edited (too old, media, deleted) gets a fresh message.
    - Message: always a new message.
    """
    if isinstance(event, CallbackQuery):
        if answer_callback:
            try:
                await event.answer()
            except TelegramBadRequest:
                pass  # query too old: still update the screen
        msg = event.message
        if isinstance(msg, Message) and msg.text is not None:
            try:
                return await msg.edit_text(
                    text,
                    reply_markup=markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
            except TelegramBadRequest as e:
                if _NOT_MODIFIED in str(e).lower():
                    return None
                logger.debug(f"render: edit failed ({e.message[:60]}), sending new message")
        if isinstance(msg, Message):
            return await msg.answer(
                text, reply_markup=markup, parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
        return await event.bot.send_message(
            event.from_user.id, text, reply_markup=markup, parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    return await event.answer(
        text, reply_markup=markup, parse_mode=parse_mode,
        disable_web_page_preview=disable_web_page_preview,
    )
