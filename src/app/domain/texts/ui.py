"""Screen kit, text side (release 3.0): the dictionary and the 12 screen types.

Every message the bot shows is a ``Screen`` of one TYPE. The type owns the
layout (header line, blockquote blocks, hint, spacing); a screen only owns its
words. See docs/SCREENS.md.

Pure: no aiogram, so services (push notifications) use it too. Keyboards are
laid out by app.bot.views.kit.

Contract: every string that goes into a ``Screen`` (title, block lines, hint)
is already HTML-safe. Build lines with ``field()`` / ``h()``; constants are
written HTML-safe by hand.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Union

from app.domain.texts import h

# =========================================================================== dictionary


class E:
    """Emoji. One meaning, one emoji, everywhere."""

    # result / push kinds
    OK = "✅"
    WAIT = "⏳"
    INFO = "ℹ️"
    WARN = "⚠️"
    ERROR = "❌"
    # subscription status
    ACTIVE = "🟢"
    GRACE = "🟡"
    EXPIRED = "🔴"
    NONE = "💡"
    # sections and subjects
    PROFILE = "👤"
    CONNECT = "🚀"
    SUBSCRIPTION = "💳"
    PAYMENT = "💳"
    OBHOD = "🛡"
    DEVICES = "📱"
    DEVICE_DESKTOP = "💻"
    DEVICE_OTHER = "📶"
    LINK = "🔗"
    HOWTO = "💡"
    GIFT = "🎁"
    PROMO = "🎉"
    REFUND = "↩️"
    AUTOPAY = "🔁"
    LOCK = "🔒"
    ASK = "❓"
    INPUT = "✍️"
    ID = "🆔"
    DATE = "📅"
    LEFT = "⏳"
    HELP = "ℹ️"
    FAQ = "❓"
    VPN = "🔐"
    MONEY = "💰"
    BELL = "🔔"
    MUTE = "🔕"
    ADMIN = "👑"


class B:
    """Button labels. A label edited here changes on every screen."""

    CONNECT = "🚀 Подключиться"
    SUBSCRIPTION = "💳 Подписка"
    RENEW = "💳 Продлить подписку"
    PAY_PREFIX = "💳 Оплатить"
    CHECK_PAYMENT = "🔄 Проверить оплату"
    PAY_STARS = "⭐ Оплатить звездами ({stars})"
    AUTOPAY_ON = "🔁 Включить автопродление"
    AUTOPAY_OFF = "🔁 Без автопродления"
    AUTOPAY_STOP = "🔁 Отключить автопродление"
    DEVICES = "📱 Мои устройства"
    OBHOD_MORE = "➕ Докупить обход"
    OPEN_LINK = "🔗 Открыть основную ссылку"
    OPEN_OBHOD = "🛡 Открыть ссылку обхода"
    ARTICLE = "📖 Инструкция"
    SUPPORT = "✍️ Поддержка"
    WRITE_ADMIN = "✍️ Написать"
    OFFER = "📄 Оферта"
    PRIVACY = "🔒 Политика конфиденциальности"
    TRIAL = "🎁 Попробовать 5 дней бесплатно"
    GIFT = "🎁 Подарить подписку"
    REFUND = "↩️ Не смог подключиться"
    REFRESH = "🔄 Обновить"
    HELP = "ℹ️ Помощь"
    ADMIN_PANEL = "👑 Админ-панель"
    UNLINK = "❌ Отвязать: {name}"  # 2.x/early 3.0 label, kept for old keyboards in chats
    UNLINK_N = "❌ {n}"  # numbered unlink button under device cards
    YES_PREFIX = "✅ Да"
    CANCEL = "✖️ Отмена"
    TO_LIST = "⬅️ К списку"
    # footer
    BACK = "⬅️ Назад"
    MENU = "🏠 В меню"
    BACK_ADMIN = "👑 В админку"
    # admin wizards (broadcast)
    BC_ABORT = "✖️ Отменить рассылку"
    # admin decisions
    APPROVE = "✅ {label}"
    REJECT = "❌ {label}"
    PREV = "⬅️"
    NEXT = "➡️"


# Result / push kinds -> header emoji.
KIND_EMOJI = {"ok": E.OK, "wait": E.WAIT, "info": E.INFO, "warn": E.WARN, "error": E.ERROR}

# The 12 types (docs/SCREENS.md). Values are the ids used in the catalog.
TYPES = (
    "status", "choice", "checkout", "result", "article", "items", "confirm", "prompt",
    "push", "toast", "admin_screen", "admin_alert",
)
TYPE_TITLES = {
    "status": "Карточка статуса",
    "choice": "Выбор",
    "checkout": "Оплата",
    "result": "Результат",
    "article": "Инструкция",
    "items": "Список",
    "confirm": "Подтверждение",
    "prompt": "Ввод",
    "push": "Уведомление",
    "toast": "Всплывашка",
    "admin_screen": "Экран админки",
    "admin_alert": "Алерт админам",
}

TOAST_MAX = 200

# =========================================================================== layout primitives


def field(label: str, value: object) -> str:
    """``Метка: значение`` with the value escaped (label is a constant)."""
    return f"{label}: {h(value)}"


def bold(text: str) -> str:
    return f"<b>{text}</b>"


def code(value: object) -> str:
    return f"<code>{h(value)}</code>"


def link(text: str, url: str) -> str:
    return f'<a href="{h(url)}">{text}</a>'


def title_line(emoji: str, title: str) -> str:
    """``{emoji} <b>{title}</b>``; emoji is outside the bold."""
    if not title:
        return ""
    return f"{emoji} <b>{title}</b>" if emoji else f"<b>{title}</b>"


Line = Union[str, None]


def _lines(lines: Iterable[Line]) -> tuple[str, ...]:
    return tuple(x for x in lines if x)


@dataclass(frozen=True)
class Block:
    """One body block: an optional title line and a blockquote (or a plain
    paragraph with ``quote=False``, used for ``<code>`` links and the legal line)."""

    lines: tuple[str, ...]
    title: str = ""
    emoji: str = ""
    quote: bool = True

    def html(self) -> str:
        head = title_line(self.emoji, self.title)
        body = "\n".join(self.lines)
        if body and self.quote:
            body = f"<blockquote>{body}</blockquote>"
        return "\n".join(x for x in (head, body) if x)


def block(*lines: Line, title: str = "", emoji: str = "", quote: bool = True) -> Optional[Block]:
    """A block of non-empty lines; None when there is nothing to show."""
    ls = _lines(lines)
    if not ls and not title:
        return None
    return Block(ls, title=title, emoji=emoji, quote=quote)


def plain(*lines: Line) -> Optional[Block]:
    return block(*lines, quote=False)


@dataclass(frozen=True)
class Screen:
    """A screen of one of the 12 TYPES. ``html()`` is the only layout code."""

    type: str
    title: str = ""
    emoji: str = ""
    blocks: tuple[Block, ...] = ()
    hint: str = ""

    def html(self) -> str:
        return render(self)


def render(screen: Screen) -> str:
    """Layout rules (docs/SCREENS.md, "Общие правила текста"):
    header; the first untitled block sticks to the header; every other part is
    separated by one blank line; the hint is the last line, in italics."""
    head = title_line(screen.emoji, screen.title)
    parts: list[str] = []
    blocks = list(screen.blocks)
    if head:
        if blocks and not blocks[0].title and blocks[0].quote:
            parts.append(head + "\n" + blocks.pop(0).html())
        else:
            parts.append(head)
    parts.extend(b.html() for b in blocks)
    if screen.hint:
        parts.append(f"<i>{screen.hint}</i>")
    return "\n\n".join(p for p in parts if p)


def _blocks(items: Iterable[Optional[Block]]) -> tuple[Block, ...]:
    return tuple(b for b in items if b is not None)


# =========================================================================== type builders


def status(sections: Sequence[Optional[Block]], *, hint: str = "") -> Screen:
    """1. Status card (main menu): titled sections, no common header."""
    return Screen("status", blocks=_blocks(sections), hint=hint)


def choice(title: str, *, emoji: str, intro: Sequence[Line] = (), options: Sequence[Optional[Block]] = (),
           hint: str = "") -> Screen:
    """2. Choice: intro quote, option descriptions, «pick below» hint."""
    return Screen("choice", title, emoji, _blocks([block(*intro), *options]), hint)


def checkout(title: str, fields: Sequence[Line], *, hint: str = "", legal: str = "",
             emoji: str = E.PAYMENT) -> Screen:
    """3. Checkout: the bill as ``label: value`` lines, how-to hint, legal line."""
    blocks = [block(*fields)]
    if hint:
        blocks.append(plain(f"<i>{hint}</i>"))
    if legal:
        blocks.append(plain(f"<i>{legal}</i>"))
    return Screen("checkout", title, emoji, _blocks(blocks))


def result(kind: str, title: str, *lines: Line, hint: str = "", extra: Sequence[Optional[Block]] = ()) -> Screen:
    """4. Result of an action. ``kind``: ok | wait | info | warn | error."""
    return Screen("result", title, KIND_EMOJI[kind], _blocks([block(*lines), *extra]), hint)


def article(title: str, *, emoji: str, sections: Sequence[Optional[Block]], hint: str = "") -> Screen:
    """5. Article: several titled sections (connect, help)."""
    return Screen("article", title, emoji, _blocks(sections), hint)


def items(title: str, *, emoji: str, lines: Sequence[Line] = (), cards: Sequence[Optional[Block]] = (),
          empty: str = "", hint: str = "") -> Screen:
    """6. List of items: one line each (``lines``) or one quote card each (``cards``, see ``card``);
    ``empty`` replaces the list when there is nothing."""
    cs = _blocks(cards)
    if cs:
        return Screen("items", title, emoji, cs, hint)
    ls = _lines(lines) or ((empty,) if empty else ())
    return Screen("items", title, emoji, _blocks([block(*ls)]), hint)


def card(n: int, name: str, *lines: Line, emoji: str = "") -> Optional[Block]:
    """One numbered card of an ``items`` list: ``{emoji} <b>{n}. {name}</b>`` and its detail
    lines, all inside one blockquote. The number matches the item's ``❌ {n}`` button."""
    head = f"<b>{int(n)}. {name}</b>"
    return block(f"{emoji} {head}" if emoji else head, *lines)


def confirm(question: str, *lines: Line) -> Screen:
    """7. Confirmation: a question and its consequences."""
    return Screen("confirm", question, E.ASK, _blocks([block(*lines)]))


def prompt(title: str, *lines: Line, hint: str = "") -> Screen:
    """8. Input prompt: what to send and how to cancel."""
    return Screen("prompt", title, E.INPUT, _blocks([block(*lines)]), hint)


def push(emoji: str, title: str, *lines: Line, hint: str = "", extra: Sequence[Optional[Block]] = ()) -> Screen:
    """9. Push notification: like a result, but its own emoji and no footer."""
    if emoji in KIND_EMOJI:
        emoji = KIND_EMOJI[emoji]
    return Screen("push", title, emoji, _blocks([block(*lines), *extra]), hint)


def toast(text: str) -> str:
    """10. Toast (callback alert): plain text up to 200 characters, no tags."""
    if len(text) > TOAST_MAX or "<" in text:
        raise ValueError(f"toast must be plain text <= {TOAST_MAX} chars: {text[:40]!r}")
    return text


def admin_screen(title: str, *, emoji: str, sections: Sequence[Optional[Block]] = (),
                 lines: Sequence[Line] = (), hint: str = "") -> Screen:
    """11. Admin card / table: an untitled quote of fields and/or titled sections."""
    return Screen("admin_screen", title, emoji, _blocks([block(*lines), *sections]), hint)


def who_block(*, name: Optional[str], username: Optional[str], telegram_id: Optional[int]) -> Optional[Block]:
    """The «who» quote of admin alerts: name, @username, id."""
    return block(
        f"{E.PROFILE} {h(name)}" + (f" @{h(username)}" if username else "") if name or username else None,
        f"{E.ID} <code>{int(telegram_id)}</code>" if telegram_id is not None else None,
    )


def admin_alert(title: str, *, emoji: str, who: Optional[Block] = None, lines: Sequence[Line] = (),
                sections: Sequence[Optional[Block]] = (), hint: str = "") -> Screen:
    """12. Admin alert: event title, who, details, what to do."""
    return Screen("admin_alert", title, emoji, _blocks([who, block(*lines), *sections]), hint)


__all__ = [
    "B", "E", "KIND_EMOJI", "TYPES", "TYPE_TITLES", "TOAST_MAX", "Block", "Screen",
    "admin_alert", "admin_screen", "article", "block", "bold", "card", "checkout", "choice", "code", "confirm",
    "field", "items", "link", "plain", "prompt", "push", "render", "result", "status", "title_line",
    "toast", "who_block",
]
