"""
Единая логика формирования username для Remnawave.

Правила (в порядке приоритета):
  1. Telegram @username  → tg_<username>
  2. first_name + last_name (транслитерация) → tg_<First>_<Last>
  3. Только first_name (транслитерация)       → tg_<First>
  4. Fallback                                 → tg_<telegram_id>

telegram_id ВСЕГДА передается в отдельное поле API, не зашивается в username.

Ограничения Remnawave 3.4.3 (create-user): username только ASCII
[A-Za-z0-9_-], длина 3..36. Все ветки ниже гарантируют это: после транслитерации
все, что не ASCII, заменяется на "_", результат обрезается до 36 символов,
пустой результат уходит в fallback tg_<telegram_id>.

Функции влияют ТОЛЬКО на новых юзеров: существующие юзеры Remnawave ищутся по
telegramId / сохраненному id и никогда не переименовываются.

ВНИМАНИЕ: буквы U+0451 / U+0401 (е и Е с двумя точками) записаны в таблице
escape-последовательностями. Не трогать их при массовых заменах этой буквы: такая правка
уже ломала транслитерацию (Сергей → tg_Sergyoy).
"""
import re
from typing import Optional

# Таблица транслитерации кириллица → латиница (ГОСТ-подобная)
_TRANSLIT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d",
    "е": "e", "\u0451": "yo", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D",
    "Е": "E", "\u0401": "Yo", "Ж": "Zh", "З": "Z", "И": "I",
    "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N",
    "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T",
    "У": "U", "Ф": "F", "Х": "Kh", "Ц": "Ts", "Ч": "Ch",
    "Ш": "Sh", "Щ": "Shch", "Ъ": "", "Ы": "Y", "Ь": "",
    "Э": "E", "Ю": "Yu", "Я": "Ya",
}


# Ограничения username в Remnawave 3.4.3 (create-user.command.ts).
REMNA_USERNAME_MAX_LEN = 36
REMNA_USERNAME_MIN_LEN = 3
_REMNA_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,36}$")


def is_valid_remna_username(name: Optional[str]) -> bool:
    """True если строка проходит валидацию username Remnawave 3.x."""
    return bool(name) and bool(_REMNA_USERNAME_RE.match(name))


def _translit(text: str) -> str:
    """Транслитерирует кириллицу в латиницу, остальное оставляет как есть."""
    return "".join(_TRANSLIT.get(ch, ch) for ch in text)


def _clean(part: str) -> str:
    """Транслит + только ASCII [A-Za-z0-9_], схлопывает повторы "_".

    Раньше тут был \\w (Unicode): «Іван» → tg_Іvan, «李» → tg_李, и Remnawave
    отвечал 400 на create. Теперь все не-ASCII после транслита → "_".
    """
    part = _translit(part)
    part = re.sub(r"[^A-Za-z0-9_]", "_", part)   # спецсимволы и не-ASCII → _
    part = re.sub(r"_+", "_", part)       # множественные _ → одиночный
    part = part.strip("_")
    return part


def _fit(candidate: str, telegram_id: int, max_len: int) -> str:
    """Обрезает до max_len и валидирует; при неудаче fallback tg_<telegram_id>."""
    candidate = candidate[:max_len].rstrip("_-")
    if is_valid_remna_username(candidate) and len(candidate) <= max_len:
        return candidate
    return f"tg_{telegram_id}"[:max_len]


def build_remna_username(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    max_len: int = REMNA_USERNAME_MAX_LEN,
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
        >>> build_remna_username(123, username="test_user")
        'tg_test_user'
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
            return _fit(f"tg_{cleaned}", telegram_id, max_len)

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
        return _fit(f"tg_{'_'.join(parts)}", telegram_id, max_len)

    # 3. Fallback
    return f"tg_{telegram_id}"[:max_len]


def build_remna_display_name(
    telegram_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
) -> str:
    """
    Формирует человекочитаемое имя для отображения в Remnawave-админке.
    Кириллица остается как есть (для читаемости).

    Examples:
        >>> build_remna_display_name(123, first_name="Ольга", last_name="Козлова")
        'Ольга Козлова'
        >>> build_remna_display_name(123, username="test_user")
        '@test_user'
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
