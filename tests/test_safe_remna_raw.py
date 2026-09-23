"""raw_data в remna_users не должен хранить ключи доступа к VPN."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import remna_service
from app.services.remna_service import safe_remna_raw

SECRET_FIELDS = ("vlessUuid", "trojanPassword", "ssPassword", "shortUuid", "subscriptionUrl")

PANEL_USER = {
    "id": 900000301,
    "uuid": "00000000-0000-4000-8000-000000000301",
    "username": "tg_900000301",
    "telegramId": 900000301,
    "status": "ACTIVE",
    "expireAt": "2026-10-01T00:00:00.000Z",
    "createdAt": "2026-09-01T00:00:00.000Z",
    "vlessUuid": "11111111-1111-4111-8111-111111111111",
    "trojanPassword": "secret-trojan",
    "ssPassword": "secret-ss",
    "shortUuid": "shortsecret",
    "subscriptionUrl": "https://sub.example.invalid/shortsecret",
}


def test_safe_raw_keeps_only_safe_fields():
    out = safe_remna_raw(PANEL_USER)
    assert set(out) == {"id", "uuid", "username", "telegramId", "status", "expireAt", "createdAt"}
    for f in SECRET_FIELDS:
        assert f not in out


def test_safe_raw_unwraps_response():
    out = safe_remna_raw({"response": PANEL_USER})
    assert out["uuid"] == PANEL_USER["uuid"]
    assert "vlessUuid" not in out


def test_safe_raw_non_dict():
    assert safe_remna_raw(None) is None
    assert safe_remna_raw("x") is None


@pytest.mark.asyncio
async def test_persist_remna_link_never_writes_secrets():
    captured = {}

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            params = stmt.compile().params
            if "raw_data" in params:
                captured["raw_data"] = params["raw_data"]
            return MagicMock()

        async def commit(self):
            return None

    with patch("app.db.session.SessionLocal", lambda: _Session()):
        ok = await remna_service.persist_remna_link(900000301, 900000301, "tg_900000301", raw_data=PANEL_USER)

    assert ok is True
    raw = captured.get("raw_data")
    assert raw is not None
    for f in SECRET_FIELDS:
        assert f not in raw
