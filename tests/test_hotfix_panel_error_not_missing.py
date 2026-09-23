"""Хотфикс 2.1, п.13 (06 M3): ошибка панели != «юзера нет», дубли не создаются."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.remnawave.client import RemnaClient, pick_primary_remna_user


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://panel.example/api/users/stream")
    return httpx.HTTPStatusError(str(code), request=req, response=httpx.Response(code, request=req))


@pytest.mark.asyncio
async def test_lenient_lookup_keeps_old_behaviour():
    client = RemnaClient()
    with patch.object(client, "request", AsyncMock(side_effect=_status_error(502))):
        assert await client.get_user_by_telegram_id(1) is None


@pytest.mark.asyncio
async def test_strict_lookup_raises_on_panel_error_but_not_on_404():
    client = RemnaClient()
    with patch.object(client, "request", AsyncMock(side_effect=_status_error(502))):
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_user_by_telegram_id(1, strict=True)
    with patch.object(client, "request", AsyncMock(side_effect=_status_error(404))):
        assert await client.get_user_by_telegram_id(1, strict=True) is None
    with patch.object(client, "request", AsyncMock(return_value={"response": {"users": []}})):
        assert await client.get_user_by_telegram_id(1, strict=True) is None


@pytest.mark.asyncio
async def test_get_or_create_does_not_create_when_panel_is_down():
    client = RemnaClient()
    create = AsyncMock()
    with patch.object(client, "request", AsyncMock(side_effect=_status_error(503))), \
         patch.object(client, "create_user", create):
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_or_create_user(telegram_id=1, tg_username="x")
    create.assert_not_awaited()


def test_duplicate_telegram_id_prefers_active_latest():
    users = [
        {"id": 1001, "status": "ACTIVE", "expireAt": "2000-01-01T00:00:00Z"},
        {"id": 1002, "status": "ACTIVE", "expireAt": "2026-12-01T00:00:00Z"},
        {"id": 1003, "status": "EXPIRED", "expireAt": "2027-12-01T00:00:00Z"},
    ]
    assert pick_primary_remna_user(users)["id"] == 1002


@pytest.mark.asyncio
async def test_lookup_returns_primary_of_duplicates():
    client = RemnaClient()
    resp = {"response": {"users": [
        {"id": 1001, "status": "ACTIVE", "expireAt": "2000-01-01T00:00:00Z", "username": "tg_test_user_a"},
        {"id": 1002, "status": "ACTIVE", "expireAt": "2026-12-01T00:00:00Z", "username": "tg_test_user_b"},
    ]}}
    with patch.object(client, "request", AsyncMock(return_value=resp)):
        user = await client.get_user_by_telegram_id(900000001)
    assert user.uuid == "1002"
