"""Screen kit, keyboard side (release 3.0). See docs/SCREENS.md.

A view is ``kit.view(screen, primary=..., options=..., secondary=..., links=..., footer=...)``:
the text comes from the Screen (app.domain.texts.ui), the keyboard is laid out
here, always in the same order:

    primary -> options -> secondary -> links -> footer

Each element of a group is one row: a single button, or a tuple of buttons
for the declared pairs (``[Обновить | Помощь]``, ``[Да | Отмена]``, pagers,
admin decisions). ``None`` elements are skipped, so conditional buttons are
written inline.

``View`` is a ``(text, markup)`` tuple (routers keep ``text, kb = ...`` and
``render(event, *view)``) that also carries the Screen for the catalog.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence, Union

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import Adm, Nav
from app.bot.views import btn, url_btn
from app.domain.texts.ui import B, Screen

Button = InlineKeyboardButton
Row = Union[Button, Sequence[Optional[Button]], None]


def action(text: str, cb: Union[CallbackData, str]) -> Button:
    return btn(text, cb)


def link(text: str, url: str) -> Button:
    return url_btn(text, url)


# ------------------------------------------------------------------------- footer


@dataclass(frozen=True)
class Footer:
    """The last row. Use the constructors below, not the fields."""

    back: Optional[Union[CallbackData, str]] = None
    menu: bool = False
    admin: bool = False
    back_label: str = B.BACK
    cancel: Optional[Union[CallbackData, str]] = None
    cancel_label: str = B.CANCEL

    @staticmethod
    def none() -> "Footer":
        return Footer()

    @staticmethod
    def to_menu() -> "Footer":
        """First-level screen (opened from the main menu): [🏠 В меню]."""
        return Footer(menu=True)

    @staticmethod
    def back_menu(back: Union[CallbackData, str]) -> "Footer":
        """Deeper screen: [⬅️ Назад] [🏠 В меню]."""
        return Footer(back=back, menu=True)

    @staticmethod
    def back_only(back: Union[CallbackData, str], label: str = B.BACK) -> "Footer":
        """Admin sub-screens: [⬅️ Назад]."""
        return Footer(back=back, back_label=label)

    @staticmethod
    def to_admin() -> "Footer":
        """Admin screens: [👑 В админку]."""
        return Footer(admin=True)

    @staticmethod
    def back_admin(back: Union[CallbackData, str], label: str = B.BACK) -> "Footer":
        """Admin sub-screens: [⬅️ Назад] [👑 В админку]."""
        return Footer(back=back, back_label=label, admin=True)

    @staticmethod
    def wizard(cancel: Union[CallbackData, str], cancel_label: str,
               back: Optional[Union[CallbackData, str]] = None) -> "Footer":
        """Admin wizard step: [⬅️ Назад] (to the previous step, if any) on its own row,
        then [✖️ Отменить ...] [👑 В админку]."""
        return Footer(back=back, admin=True, cancel=cancel, cancel_label=cancel_label)

    def rows(self) -> list[list[Button]]:
        if self.cancel is not None:
            out = [[btn(self.back_label, self.back)]] if self.back is not None else []
            last = [btn(self.cancel_label, self.cancel)]
            if self.admin:
                last.append(btn(B.BACK_ADMIN, ADMIN_CB))
            return out + [last]
        row = self.row()
        return [row] if row else []

    def row(self) -> list[Button]:
        """The single footer row (a wizard footer has two rows: use ``rows``)."""
        out: list[Button] = []
        if self.back is not None:
            out.append(btn(self.back_label, self.back))
        if self.menu:
            out.append(btn(B.MENU, MENU_CB))
        if self.admin:
            out.append(btn(B.BACK_ADMIN, ADMIN_CB))
        return out


MENU_CB = Nav(s="main")
ADMIN_CB = Adm(s="panel", a="open")


# ------------------------------------------------------------------------- keyboard


def _row(r: Row) -> list[Button]:
    if r is None:
        return []
    if isinstance(r, InlineKeyboardButton):
        return [r]
    return [b for b in r if b is not None]


def keyboard(*, primary: Iterable[Row] = (), options: Iterable[Row] = (), secondary: Iterable[Row] = (),
             links: Iterable[Row] = (), footer: Footer = Footer()) -> InlineKeyboardMarkup:
    rows: list[list[Button]] = []
    for group in (primary, options, secondary, links):
        for r in group:
            line = _row(r)
            if line:
                rows.append(line)
    rows.extend(footer.rows())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def grid(buttons: Sequence[Optional[Button]], per_row: int = 4) -> list[tuple[Button, ...]]:
    """Short numbered buttons (``❌ 1``, ``❌ 2``...) in balanced rows of at most ``per_row``
    (5 -> 3+2, 6 -> 3+3, 7 -> 4+3). Declared in docs/SCREENS.md for item lists."""
    bs = [b for b in buttons if b is not None]
    if not bs:
        return []
    n_rows = -(-len(bs) // per_row)
    size = -(-len(bs) // n_rows)
    return [tuple(bs[i:i + size]) for i in range(0, len(bs), size)]


def pair(left: Optional[Button], right: Optional[Button]) -> tuple[Optional[Button], Optional[Button]]:
    """A declared two-button row (see docs/SCREENS.md)."""
    return (left, right)


# ------------------------------------------------------------------------- view


class View(tuple):
    """``(text, markup)`` plus the Screen it was built from."""

    screen: Optional[Screen]

    def __new__(cls, text: str, markup: Optional[InlineKeyboardMarkup], screen: Optional[Screen] = None):
        obj = super().__new__(cls, (text, markup))
        obj.screen = screen
        return obj

    @property
    def text(self) -> str:
        return self[0]

    @property
    def markup(self) -> Optional[InlineKeyboardMarkup]:
        return self[1]

    @property
    def type(self) -> Optional[str]:
        return self.screen.type if self.screen is not None else None


def view(screen: Screen, *, primary: Iterable[Row] = (), options: Iterable[Row] = (),
         secondary: Iterable[Row] = (), links: Iterable[Row] = (), footer: Footer = Footer()) -> View:
    markup = keyboard(primary=primary, options=options, secondary=secondary, links=links, footer=footer)
    return View(screen.html(), markup, screen)


def markup_only(**parts: Any) -> Optional[InlineKeyboardMarkup]:
    """Keyboard for a push sent by a service; None when it has no buttons."""
    m = keyboard(**parts)
    return m if m.inline_keyboard else None


__all__ = ["ADMIN_CB", "MENU_CB", "Footer", "View", "action", "grid", "keyboard", "link", "markup_only", "pair",
           "view"]
