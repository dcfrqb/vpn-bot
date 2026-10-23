"""Refund adapter confirms remote rights using mocked transport only."""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import obhod_service as ob
from app.services.provisioning_rules import LegacyObhodSync

UNTIL = datetime(2026, 12, 1)


@pytest.fixture
def setup_sync(monkeypatch):
    sub = SimpleNamespace(remna_user_id="88", active=True)
    session = AsyncMock()
    client = AsyncMock()
    monkeypatch.setattr(ob, "get_obhod_subscription", AsyncMock(return_value=sub))
    monkeypatch.setattr(ob, "RemnaClient", lambda: client)
    return sub, session, client


async def test_failed_disable_keeps_active_row_for_retry(setup_sync):
    sub, session, client = setup_sync
    client.get_user_by_id.return_value = {"response": {"status": "ACTIVE"}}
    client.disable_user.side_effect = OSError("offline")
    assert not await ob.sync_obhod_after_refund(session, 7, "lite", UNTIL, "r")
    assert sub.active
    session.commit.assert_not_awaited()


async def test_lost_disable_response_confirmed_by_read(setup_sync):
    sub, session, client = setup_sync
    client.get_user_by_id.side_effect = [
        {"response": {"status": "ACTIVE"}}, {"response": {"status": "DISABLED"}},
    ]
    client.disable_user.side_effect = TimeoutError()
    assert await ob.sync_obhod_after_refund(session, 7, "lite", UNTIL, "r")
    assert not sub.active
    session.commit.assert_awaited_once()


async def test_inactive_row_still_disables_remote_user(setup_sync):
    sub, session, client = setup_sync
    sub.active = False
    client.get_user_by_id.side_effect = [
        {"response": {"status": "ACTIVE"}}, {"response": {"status": "DISABLED"}},
    ]
    assert await ob.sync_obhod_after_refund(session, 7, None, UNTIL, "r")
    client.disable_user.assert_awaited_once_with("88")


@pytest.mark.parametrize("foreign", [False, True])
async def test_owned_orphan_only(monkeypatch, setup_sync, foreign):
    _, session, client = setup_sync
    monkeypatch.setattr(ob, "get_obhod_subscription", AsyncMock(return_value=None))
    client.get_user_by_username.return_value = {
        "username": "tg_7_obhod", "telegramId": 99 if foreign else None, "uuid": "88",
    }
    client.get_user_by_id.side_effect = [
        {"response": {"status": "ACTIVE"}}, {"response": {"status": "DISABLED"}},
    ]
    assert await ob.sync_obhod_after_refund(session, 7, "lite", UNTIL, "r") is (not foreign)
    assert client.disable_user.await_count == (0 if foreign else 1)


@pytest.mark.parametrize("status,expiry,squads,expected", [
    ("ACTIVE", "2026-12-01T00:00:00Z", [{"uuid": "ob"}], True),
    ("LIMITED", "2026-12-01T00:00:00Z", [{"uuid": "ob"}], True),
    ("DISABLED", "2026-12-01T00:00:00Z", [{"uuid": "ob"}], False),
    ("ACTIVE", "2027-01-01T00:00:00Z", [{"uuid": "ob"}], False),
    ("ACTIVE", "2026-12-01T00:00:00Z", [{"uuid": "other"}], False),
])
async def test_pro_requires_remote_confirmation(monkeypatch, setup_sync, status, expiry, squads, expected):
    _, session, client = setup_sync
    monkeypatch.setattr(ob, "ensure_obhod_for_pro", AsyncMock(return_value="https://sub/88"))
    client.get_squad_by_name.return_value = {"uuid": "ob"}
    client.get_user_by_id.return_value = {"response": {
        "status": status, "expireAt": expiry, "activeInternalSquads": squads,
    }}
    assert await ob.sync_obhod_after_refund(session, 7, "pro", UNTIL, "r") is expected


async def test_adapter_without_session_cannot_confirm(monkeypatch):
    adapter = LegacyObhodSync()
    monkeypatch.setattr(adapter, "_session", AsyncMock(return_value=None))
    assert not await adapter.on_main_refunded(7, "lite", UNTIL, "r")
