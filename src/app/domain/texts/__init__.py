"""User-facing text helpers (release 3.0 Foundation).

Helpers live here; the texts themselves live in per-area modules owned by
streams (``common``, ``menu``, ``checkout``, ``connect``, ``devices``,
``promo``, ``admin``, ``notify``).

Rules for every text module:
- Russian prose without the letter «ё» (a test enforces it).
- Values interpolated into HTML go through ``h()``.
- Numbers with units go through ``plural_ru``/``n_plural``; money through
  ``fmt_rub``; dates through ``fmt_date_msk``.
"""
from __future__ import annotations

import html as _html
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, Union

# Moscow has had no DST since 2014; a fixed offset avoids a tzdata dependency
# in the slim Docker image.
MSK = timezone(timedelta(hours=3), name="MSK")

NBSP = " "


def h(value: Any) -> str:
    """HTML-escape any value for Telegram parse_mode=HTML. None -> ""."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form for n: plural_ru(5, "день", "дня", "дней") -> "дней"."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def n_plural(n: int, one: str, few: str, many: str) -> str:
    """Number with its word: n_plural(3, "день", "дня", "дней") -> "3 дня"."""
    return f"{int(n)}{NBSP}{plural_ru(n, one, few, many)}"


def days_ru(n: int) -> str:
    return n_plural(n, "день", "дня", "дней")


def months_ru(n: int) -> str:
    return n_plural(n, "месяц", "месяца", "месяцев")


def devices_ru(n: int) -> str:
    return n_plural(n, "устройство", "устройства", "устройств")


def to_msk(dt: datetime) -> datetime:
    """Aware or naive-UTC datetime -> Moscow time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK)


def fmt_date_msk(value: Optional[Union[datetime, date]], *, with_time: bool = False, empty: str = "—") -> str:
    """01.10.2026 (or 01.10.2026 15:30 with_time) in Moscow time.

    Naive datetimes are treated as UTC (that is how the DB stores them).
    A bare ``date`` is printed as is.
    """
    if value is None:
        return empty
    if isinstance(value, datetime):
        value = to_msk(value)
        return value.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")
    return value.strftime("%d.%m.%Y")


def fmt_rub(amount: Union[int, float, Decimal, str, None]) -> str:
    """1199 -> "1 199 ₽" (non-breaking spaces); kopecks shown only if non-zero."""
    if amount is None:
        return f"0{NBSP}₽"
    d = Decimal(str(amount))
    if d == d.to_integral_value():
        body = f"{int(d):,}".replace(",", NBSP)
    else:
        body = f"{d:,.2f}".replace(",", NBSP).replace(".", ",")
    return f"{body}{NBSP}₽"


def fmt_gb(n_bytes: Optional[int]) -> str:
    """Bytes -> "12,3 ГБ" (GiB, one decimal, comma separator)."""
    if not n_bytes:
        return f"0{NBSP}ГБ"
    gb = n_bytes / (1024 ** 3)
    text = f"{gb:.1f}".replace(".", ",")
    if text.endswith(",0"):
        text = text[:-2]
    return f"{text}{NBSP}ГБ"


__all__ = [
    "MSK", "NBSP", "h", "plural_ru", "n_plural", "days_ru", "months_ru", "devices_ru",
    "to_msk", "fmt_date_msk", "fmt_rub", "fmt_gb",
]
