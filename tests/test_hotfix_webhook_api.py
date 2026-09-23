"""Хотфикс 2.1, п.11: гигиена webhook API (IP, /docs, /health, экранирование имен)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types
from fastapi.testclient import TestClient

from app.api import main as api_main


def _req(peer: str, headers: dict):
    return SimpleNamespace(client=SimpleNamespace(host=peer), headers=headers)


def test_spoofed_headers_from_internet_are_ignored():
    # прямое соединение с внешнего IP: заголовкам не верим
    req = _req("203.0.113.9", {"CF-Connecting-IP": "185.71.76.1", "X-Real-IP": "185.71.76.1",
                               "X-Forwarded-For": "185.71.76.1"})
    assert api_main._get_client_ip(req) == "203.0.113.9"
    assert not api_main._is_yookassa_ip(api_main._get_client_ip(req))


def test_behind_local_nginx_only_x_real_ip_counts():
    # nginx на хосте -> контейнер видит docker-шлюз 172.18.0.1
    req = _req("172.18.0.1", {"CF-Connecting-IP": "185.71.76.1", "X-Forwarded-For": "185.71.76.1",
                              "X-Real-IP": "203.0.113.9"})
    assert api_main._get_client_ip(req) == "203.0.113.9"
    req = _req("172.18.0.1", {"X-Real-IP": "185.71.76.5"})
    assert api_main._is_yookassa_ip(api_main._get_client_ip(req))


def test_docs_disabled():
    client = TestClient(api_main.app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


def test_health_does_not_leak_error_text():
    client = TestClient(api_main.app)

    class _Boom:
        def __call__(self):
            raise RuntimeError("password authentication failed for user crs_user at 10.0.0.5")

    redis = MagicMock()
    redis.ping = AsyncMock(side_effect=RuntimeError("secret redis host"))
    with patch("app.db.session.SessionLocal", _Boom()), \
         patch("app.services.cache.get_redis_client", return_value=redis):
        r = client.get("/health")
    assert r.status_code == 503
    body = r.text
    assert "password" not in body and "secret" not in body and "10.0.0.5" not in body


def test_webhook_rejects_spoofed_cf_header():
    client = TestClient(api_main.app)  # peer = "testclient" (не доверенный прокси)
    with patch.object(api_main, "bot_instance", object()):
        r = client.post("/webhook/yookassa", json={"event": "payment.succeeded", "object": {"id": "x"}},
                        headers={"CF-Connecting-IP": "185.71.76.1"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_friend_request_escapes_html_in_admin_message():
    from app.routers import start as start_router
    from app.services.sync_service import SyncResult

    msg = MagicMock(spec=types.Message)
    msg.from_user = types.User(id=5, is_bot=False, first_name='<a href="https://evil">Открыть панель</a>',
                               username="x")
    msg.answer = AsyncMock()
    msg.bot = AsyncMock()
    sync = MagicMock()
    sync.sync_user_and_subscription = AsyncMock(return_value=SyncResult(False, None, "none", None, "remna"))
    with patch.object(start_router, "SyncService", return_value=sync), \
         patch.object(start_router.settings, "ADMINS", [900]):
        await start_router.cmd_friend(msg)
    text = msg.bot.send_message.await_args.kwargs["text"]
    assert "<a href" not in text
    assert "&lt;a href" in text


@pytest.mark.asyncio
async def test_sun718_admin_notify_escapes():
    from app.routers import start as start_router

    bot = AsyncMock()
    with patch.object(start_router.settings, "ADMINS", [900]):
        await start_router._sun718_notify_admins(
            bot, user_id=1, username="@u", name="<b>x</b><script>", title="t", body="b",
        )
    text = bot.send_message.await_args.kwargs["text"]
    assert "<script>" not in text and "&lt;script&gt;" in text
