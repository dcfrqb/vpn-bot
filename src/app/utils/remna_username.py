"""
Единая логика формирования username для Remnawave.

Правила (в порядке приоритета):
  1. Telegram @username  → tg_<username>
  2. first_name + last_name (транслитерация) → tg_<First>_<Last>
  3. Только first_name (транслитерация)       → tg_<First>
  4. Fallback                                 → tg_<telegram_id>

telegram_id ВСЕГДА передаётся в отдельное поле API, не зашивается в username.
"""
import re
from typing import Optional

# Таблица транслитерации кириллица → латиница (ГОСТ-подобная)
_TRANSLIT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
    "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D",
    "Е": "E", "Ё": "Yo", "Ж": "Zh", "З": "Z", "И": "I",
    "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N",
    "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T",
    "У": "U", "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch",
    "Ш": "Sh", "Щ": "Shch", "Ъ": "", "Ы": "Y", "Ь": "",
    "Э": "E", "Ю": "Yu", "Я": "Ya",
}


def _translit(text: str) -> str:
    """Транслитерирует кириллицу в латиницу, остальное оставляет как есть."""
    return "".join(_TRANSLIT.get(ch, ch) for ch in text)


def _clean(part: str) -> str:
    """Убирает всё, кроме букв/цифр/подчёркивания, схлопывает повторы."""
    part = _translit(part)
    part = re.sub(r"[^\w]", "_", part)   # спецсимволы → _
    part = re.sub(r"_+", "_", part)       # множественные _ → одиночный
    part = part.strip("_")
    return part


def build_remna_username(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> str:
    """
    Формирует username для Remnawave.

    Args:
        telegram_id:  Telegram user ID (используется как fallback)
        username:     Telegram @username (без @)
        first_name:   Имя из Telegram
        last_name:    Фамилия из Telegram

    Returns:
        Строка вида tg_<...>, пригодная для Remnawave username.

    Examples:
        >>> build_remna_username(123, username="kozlova_olga")
        'tg_kozlova_olga'
        >>> build_remna_username(123, first_name="Ольга", last_name="Козлова")
        'tg_Olga_Kozlova'
        >>> build_remna_username(123, first_name="Иван")
        'tg_Ivan'
        >>> build_remna_username(123)
        'tg_123'
    """
    # 1. Telegram @username
    if username:
        cleaned = _clean(username)
        if cleaned:
            return f"tg_{cleaned}"

    # 2. Имя + фамилия (транслит)
    parts = []
    if first_name:
        c = _clean(first_name)
        if c:
            parts.append(c)
    if last_name:
        c = _clean(last_name)
        if c:
            parts.append(c)

    if parts:
        return f"tg_{'_'.join(parts)}"

    # 3. Fallback
    return f"tg_{telegram_id}"


def build_remna_display_name(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> str:
    """
    Формирует человекочитаемое имя для отображения в Remnawave-админке.
    Кириллица остаётся как есть (для читаемости).

    Examples:
        >>> build_remna_display_name(123, first_name="Ольга", last_name="Козлова")
        'Ольга Козлова'
        >>> build_remna_display_name(123, username="kozlova_olga")
        '@kozlova_olga'
        >>> build_remna_display_name(123)
        'User 123'
    """
    parts = []
    if first_name:
        parts.append(first_name.strip())
    if last_name:
        parts.append(last_name.strip())
    if parts:
        return " ".join(parts)
    if username:
        return f"@{username}"
    return f"User {telegram_id}"
