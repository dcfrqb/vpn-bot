"""Хотфикс 2.1, п.7: транслитерация username для Remnawave.

Буква U+0451 (е с двумя точками) в тестах записана escape-последовательностью,
чтобы массовые замены этой буквы в репо не ломали и тесты тоже.
"""
import pytest

from app.utils.remna_username import (
    REMNA_USERNAME_MAX_LEN,
    build_remna_username,
    is_valid_remna_username,
)

YO = "\u0451"
YO_UP = "\u0401"


def test_plain_e_is_e_not_yo():
    # До фикса «е» превращалась в "yo": Сергей -> tg_Sergyoy
    assert build_remna_username(1, first_name="Сергей") == "tg_Sergey"
    assert build_remna_username(1, first_name="Иван", last_name="Тестов") == "tg_Ivan_Testov"


def test_yo_is_transliterated():
    assert build_remna_username(1, first_name=f"Арт{YO}м") == "tg_Artyom"
    assert build_remna_username(1, first_name=f"{YO_UP}лка") == "tg_Yolka"


@pytest.mark.parametrize(
    "first_name,last_name",
    [
        ("Іван", None),          # украинская І
        ("李", "小龙"),           # CJK
        ("😀", None),            # эмодзи
        ("Zoë", "Müller"),       # латиница с диакритикой
    ],
)
def test_ascii_only_guard(first_name, last_name):
    name = build_remna_username(424242, first_name=first_name, last_name=last_name)
    assert is_valid_remna_username(name), name
    assert name.isascii()


def test_non_ascii_only_falls_back_to_id():
    assert build_remna_username(424242, first_name="李") == "tg_424242"


def test_length_cap_36():
    long_handle = "a" * 32  # максимальная длина @username в Telegram
    name = build_remna_username(1, username=long_handle)
    assert len(name) <= REMNA_USERNAME_MAX_LEN
    assert is_valid_remna_username(name)

    long_name = build_remna_username(1, first_name="Константинопольский", last_name="Александровский-Длинный")
    assert len(long_name) <= REMNA_USERNAME_MAX_LEN
    assert is_valid_remna_username(long_name)


def test_custom_max_len_leaves_room_for_suffix():
    name = build_remna_username(1, username="b" * 32, max_len=30)
    assert len(name) <= 30


def test_username_priority_and_fallback():
    assert build_remna_username(5, username="test_user_olga") == "tg_test_user_olga"
    assert build_remna_username(5) == "tg_5"
    assert build_remna_username(5, username="___") == "tg_5"
