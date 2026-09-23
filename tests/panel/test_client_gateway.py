"""Stream B: own httpx client fixes (06 M3, L1-L7, pagination) and HttpRemnaGateway."""
from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.infra.remnawave import client as client_mod
from app.infra.remnawave.client import RemnaClient, build_user_payload_from_kwargs, panel_id, plan_from_squad_names
from app.infra.remnawave.dto import UPDATE_FIELDS, UserDTO, devices_from_api, pick_primary
from app.infra.remnawave.gateway import HttpRemnaGateway
from tests.fakes.remnawave import FakeRemna, FakeRemnaGateway

# PATCH /api/users body of Remnawave 3.4.3 (from the panel's openapi.json).
# REMNAWAVE_OPENAPI=<path to openapi.json> re-checks against the file itself.
PATCH_FIELDS_3_4_3 = {
    "username", "id", "status", "trafficLimitBytes", "trafficLimitStrategy", "expireAt", "description",
    "tag", "telegramId", "email", "hwidDeviceLimit", "activeInternalSquads", "externalSquadUuid",
}

USER_3_4_3 = {
    "id": 303, "shortUuid": "abc", "username": "tg_x", "status": "LIMITED", "trafficLimitBytes": 0,
    "trafficLimitStrategy": "NO_RESET", "expireAt": "2026-10-06T00:00:00.000Z", "telegramId": 42,
    "email": None, "description": "Ivan", "tag": None, "hwidDeviceLimit": 5, "externalSquadUuid": None,
    "trojanPassword": "x" * 8, "vlessUuid": "00000000-0000-0000-0000-000000000001", "ssPassword": "y" * 8,
    "lastTriggeredThreshold": 0, "subRevokedAt": "2026-09-01T00:00:00Z", "lastTrafficResetAt": None,
    "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-09-01T00:00:00Z",
    "subscriptionUrl": "https://panel.example/sub/TOKEN",
    "activeInternalSquads": [{"uuid": "sq-pro-friend", "name": "pro-friend"}, {"uuid": "sq-us-2", "name": "us-2"}],
    "userTraffic": {"usedTrafficBytes": 1234, "lifetimeUsedTrafficBytes": 5000, "onlineAt": None,
                    "firstConnectedAt": None, "lastConnectedNodeUuid": None},
}


def resp(status: int, body=None, method="GET", url="https://p/api/x"):
    req = httpx.Request(method, url)
    content = json.dumps(body).encode() if body is not None else b""
    return httpx.Response(status, request=req, content=content)


@pytest.fixture
def raw_client():
    c = RemnaClient(use_shared_client=False, max_retries=0, initial_delay=0)
    c.base_url, c.api_key = "https://p", "t"
    c._own_client = AsyncMock(spec=httpx.AsyncClient)
    return c


# ------------------------------------------------------------------ DTOs vs the contract

def test_dto_fields_match_openapi_3_4_3():
    assert UPDATE_FIELDS == PATCH_FIELDS_3_4_3
    path = os.getenv("REMNAWAVE_OPENAPI")
    if not path:
        return
    spec = json.loads(pathlib.Path(path).read_text())
    schema = spec["paths"]["/api/users/"]["patch"]["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
    assert set(schema["properties"]) == UPDATE_FIELDS


def test_user_dto_parses_a_3_4_3_user():
    u = UserDTO.from_api({"response": USER_3_4_3})
    assert u.id == 303 and u.telegram_id == 42 and u.hwid_device_limit == 5 and u.used_traffic_bytes == 1234
    assert [s.name for s in u.squads] == ["pro-friend", "us-2"]
    assert u.is_live(datetime(2026, 9, 23, tzinfo=timezone.utc))  # LIMITED + revoked link = still live (L6)
    assert "TOKEN" not in repr(u)
    pu = u.to_panel_user()
    assert pu.squads == ("pro-friend", "us-2") and pu.device_limit == 5


def test_devices_and_primary():
    devs, total = devices_from_api({"response": {"total": 1, "devices": [
        {"hwid": "HWID-123456789", "userId": 1, "platform": "iOS", "osVersion": "18", "deviceModel": "iPhone",
         "userAgent": "Happ", "requestIp": "1.2.3.4", "createdAt": "2026-01-01T00:00:00Z",
         "updatedAt": "2026-02-01T00:00:00Z"}]}})
    assert total == 1 and devs[0].to_device_info().short_id == "23456789"
    a = UserDTO.from_api({"id": 1, "status": "EXPIRED", "expireAt": "2027-01-01T00:00:00Z"})
    b = UserDTO.from_api({"id": 2, "status": "ACTIVE", "expireAt": "2026-10-01T00:00:00Z"})
    c = UserDTO.from_api({"id": 3, "status": "ACTIVE", "expireAt": "2026-12-01T00:00:00Z"})
    assert pick_primary([a, b, c]).id == 3


# ------------------------------------------------------------------ L1, L3, L6

def test_payload_drops_fields_absent_in_3x():
    p = build_user_payload_from_kwargs({"name": "Ivan", "password": "x", "permissions": [], "description": "Ivan",
                                        "hwid_device_limit": "5", "expire_at": datetime(2026, 1, 1)})
    assert p == {"description": "Ivan", "hwidDeviceLimit": 5, "expireAt": "2026-01-01T00:00:00Z"}


@pytest.mark.parametrize("bad", ["", "abc", "12345678-aaaa-bbbb", "0", "-5", None, True])
def test_panel_id_validation(bad):
    with pytest.raises(ValueError):
        panel_id(bad)
    assert panel_id(" 42 ") == 42


async def test_create_user_sends_description_not_name_or_password(raw_client):
    raw_client._own_client.request = AsyncMock(return_value=resp(200, {"response": USER_3_4_3}))
    await raw_client.create_user("tg_x", "secretpass", display_name="Ivan", telegram_id=42)
    body = raw_client._own_client.request.call_args.kwargs["json"]
    assert body["description"] == "Ivan" and "name" not in body and "password" not in body
    assert body["expireAt"] == "2000-01-01T00:00:00Z"


def test_plan_from_squads():
    assert plan_from_squad_names(["us-2", "pro-friend"]) == "pro"
    assert plan_from_squad_names(["lite-m"]) == "lite"
    assert plan_from_squad_names(["arcadia", "esp"]) is None


async def test_status_logic_uses_status_not_revoked_at(raw_client):
    users = [dict(USER_3_4_3, status="DISABLED")]
    raw_client.find_users_by_telegram_id = AsyncMock(return_value=users)
    _, sub = await raw_client.get_user_with_subscription_by_telegram_id(42)
    assert sub.active is False and sub.plan == "pro"
    raw_client.find_users_by_telegram_id = AsyncMock(return_value=[dict(USER_3_4_3)])
    _, sub = await raw_client.get_user_with_subscription_by_telegram_id(42)
    assert sub.active is True  # LIMITED with subRevokedAt set


# ------------------------------------------------------------------ M3

async def test_find_by_username_raises_on_outage_so_no_duplicate(raw_client):
    raw_client._own_client.request = AsyncMock(return_value=resp(503, {"message": "down"}))
    with pytest.raises(httpx.HTTPStatusError):
        await raw_client.get_user_by_username("tg_x")
    raw_client._own_client.request = AsyncMock(return_value=resp(404, {"message": "nf"}))
    assert await raw_client.get_user_by_username("tg_x") is None


async def test_create_unique_does_not_move_on_when_owner_check_fails():
    fake = FakeRemna()
    fake.add_user(1, "tg_ivan", telegram_id=999)

    async def outage(name):
        raise httpx.ConnectError("down")

    fake._find_user_by_username = outage
    with pytest.raises(httpx.ConnectError):
        await fake.create_user_unique(telegram_id=7, base_username="tg_ivan", expire_at=None)
    assert not fake.created


async def test_find_users_filters_foreign_telegram_ids(raw_client):
    body = {"response": {"users": [dict(USER_3_4_3), dict(USER_3_4_3, id=9, telegramId=7)],
                         "nextCursor": None, "hasMore": False}}
    raw_client._own_client.request = AsyncMock(return_value=resp(200, body))
    found = await raw_client.find_users_by_telegram_id(42)
    assert [u["id"] for u in found] == [303]


# ------------------------------------------------------------------ L2 pagination, L4 logging

async def test_get_users_starts_at_offset_zero(raw_client):
    raw_client._own_client.request = AsyncMock(return_value=resp(200, {"response": {"users": [], "total": 0}}))
    await raw_client.get_users()
    assert "start=0" in raw_client._own_client.request.call_args.args[1]


async def test_404_is_debug_not_error(raw_client):
    from app.logger import logger

    lines = []
    sink = logger.add(lambda m: lines.append(m.record["level"].name), level="DEBUG")
    try:
        raw_client._own_client.request = AsyncMock(return_value=resp(404, {"m": 1}))
        with pytest.raises(httpx.HTTPStatusError):
            await raw_client.get_user_by_id(5)
    finally:
        logger.remove(sink)
    assert "ERROR" not in lines


def test_ui_client_is_short():
    c = RemnaClient.for_ui()
    assert c.max_retries == 1 and c.timeout.read <= 8


async def test_subscription_url_domain_override(raw_client, monkeypatch):
    monkeypatch.setattr(client_mod.settings, "SUBSCRIPTION_BASE_URL", "https://sub.example.com")
    raw_client._own_client.request = AsyncMock(return_value=resp(200, {"response": USER_3_4_3}))
    assert await raw_client.get_user_subscription_url("303") == "https://sub.example.com/sub/TOKEN"


# ------------------------------------------------------------------ gateway

async def test_iter_users_pages_by_offset_without_gaps_or_dups():
    fake = FakeRemna()
    for i in range(1, 8):
        fake.add_user(i, f"u{i}", telegram_id=i)
    gw = FakeRemnaGateway(fake)
    ids = [u.id async for u in gw.iter_users(page_size=3)]
    assert ids == [1, 2, 3, 4, 5, 6, 7]


async def test_squad_cache_ttl_and_refresh_on_unknown_name():
    fake = FakeRemna()
    calls = []
    real = fake.list_internal_squads

    async def counting():
        calls.append(1)
        return await real()

    fake.list_internal_squads = counting
    now = [0.0]
    gw = HttpRemnaGateway(lambda: fake, clock=lambda: now[0])
    await gw.list_squads()
    await gw.list_squads()
    assert len(calls) == 1
    now[0] = 601
    await gw.list_squads()
    assert len(calls) == 2
    fake.squads["new"] = "sq-new"
    assert await gw._names_to_uuids(["new"]) == ["sq-new"]  # refreshed once
    assert len(calls) == 3
    with pytest.raises(LookupError):
        await gw._names_to_uuids(["ghost"])


async def test_gateway_finds_all_users_primary_first(gw, fake):
    fake.add_user(1, "a", telegram_id=7, status="EXPIRED", expire="2026-01-01T00:00:00Z")
    fake.add_user(2, "b", telegram_id=7, status="ACTIVE", expire="2026-12-01T00:00:00Z")
    fake.add_user(3, "c", telegram_id=8)
    assert [u.id for u in await gw.find_users_by_telegram_id(7)] == [2, 1]
    assert await gw.find_users_by_telegram_id(9) == []
    fake.fail_lookup_tg = True
    with pytest.raises(httpx.ConnectError):
        await gw.find_users_by_telegram_id(7)


async def test_gateway_devices_roundtrip(gw, fake):
    fake.add_user(1, "a", telegram_id=7)
    fake.add_device(1, "HWID-AAAA11111111")
    fake.add_device(1, "HWID-BBBB22222222")
    devs = await gw.list_devices(1)
    assert [d.short_id for d in devs] == ["11111111", "22222222"]
    assert await gw.delete_device(1, "HWID-AAAA11111111") is True
    assert await gw.delete_device(1, "HWID-AAAA11111111") is False
    assert [d.hwid async for d in gw.iter_all_devices(page_size=1)] == ["HWID-BBBB22222222"]


async def test_gateway_get_user_404_and_legacy_id(gw):
    assert await gw.get_user(999) is None


async def test_gateway_update_is_one_patch_with_changes_only(gw, fake):
    fake.add_user(1, "a", telegram_id=7, squads=["lite", "arcadia"], limit=5)
    await gw.update_user(1, squads=["pro"], device_limit=10, expire_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert len(fake.patches) == 1
    assert set(fake.squad_names(1)) == {"pro", "arcadia"}
    await gw.update_user(1, device_limit=3)  # lower: nothing to send
    assert len(fake.patches) == 1


async def test_old_import_path_is_the_same_module():
    import app.remnawave.client as old

    assert old is client_mod and old.RemnaClient is RemnaClient
    with patch("app.remnawave.client.LIFETIME_EXPIRE_AT", "X"):
        assert client_mod.LIFETIME_EXPIRE_AT == "X"
