"""Хотфикс 2.1, п.2: захват чужого аккаунта Remnawave через совпадение username.

Сценарий: @handle освободился и его занял другой человек, либо два тезки без
@ника (tg_Ivan). Раньше бот находил существующего юзера по username, переписывал
ему telegramId и отдавал чужую подписку/ссылку. Теперь чужого не трогаем.
"""
from unittest.mock import AsyncMock

import pytest

from app.remnawave.client import RemnaClient, is_own_remna_user
from tests.fakes.remnawave import FakeRemna


def test_is_own_remna_user():
    assert is_own_remna_user({"id": 1, "telegramId": 5}, 5)
    assert not is_own_remna_user({"id": 1, "telegramId": 6}, 5)
    assert not is_own_remna_user({"id": 1, "telegramId": None}, 5)
    assert is_own_remna_user({"id": 1, "telegramId": None}, 5, known_remna_id="1")
    assert not is_own_remna_user({"id": 2, "telegramId": None}, 5, known_remna_id="1")


def _client_on(fake: FakeRemna) -> RemnaClient:
    """Настоящий RemnaClient (его логика get_or_create/create_user_unique),
    но с HTTP-операциями из фейка."""
    client = RemnaClient()
    client.create_user = fake.create_user
    client._find_user_by_username = fake._find_user_by_username
    client.get_user_by_telegram_id = fake.get_user_by_telegram_id
    client.update_user = fake.update_user
    return client


@pytest.mark.asyncio
async def test_get_or_create_does_not_take_over_foreign_account():
    fake = FakeRemna()
    victim = fake.add_user(303, "tg_handle", telegram_id=111, squads=["pro"], expire="2027-01-01T00:00:00Z")
    client = _client_on(fake)

    user = await client.get_or_create_user(telegram_id=222, tg_username="handle")

    assert user.uuid != "303"
    assert victim["telegramId"] == 111, "telegramId жертвы не переписан"
    assert all(p["id"] != 303 for p in fake.patches), "жертву не патчили"
    created = fake.users[int(user.uuid)]
    assert created["telegramId"] == 222
    assert created["username"] == "tg_222"


@pytest.mark.asyncio
async def test_get_or_create_adopts_own_user_after_failed_lookup():
    """Username занят НАШИМ же юзером (telegramId совпадает) — используем его."""
    fake = FakeRemna()
    fake.add_user(10, "tg_me", telegram_id=222)
    client = _client_on(fake)
    client.get_user_by_telegram_id = AsyncMock(return_value=None)  # лукап не нашел (сбой)

    user = await client.get_or_create_user(telegram_id=222, tg_username="me")
    assert user.uuid == "10"
    assert fake.created == []


@pytest.mark.asyncio
async def test_namesakes_without_handle_get_distinct_accounts():
    fake = FakeRemna()
    client = _client_on(fake)
    first = await client.get_or_create_user(telegram_id=1, tg_first_name="Иван")
    second = await client.get_or_create_user(telegram_id=2, tg_first_name="Иван")
    assert first.uuid != second.uuid
    assert fake.users[int(first.uuid)]["telegramId"] == 1
    assert fake.users[int(second.uuid)]["telegramId"] == 2


class _Res:
    def __init__(self, obj):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


# test_payment_path_does_not_extend_or_rebind_victim: removed in 3.0 with the 2.x provisioning (the grant path is stream B, tests/panel)
