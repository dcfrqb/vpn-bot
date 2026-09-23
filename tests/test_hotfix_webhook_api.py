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


@pytest.fixture
def docker_gateway_172_18():
    """Как в контейнере: шлюз сети compose 172.18.0.1 (фикс-раунд 1: доверяем
    только ему и localhost, а не всем частным сетям)."""
    api_main._parse_trusted_proxies.cache_clear()
    with patch.object(api_main, "_docker_default_gateway", return_value="172.18.0.1"):
        yield
    api_main._parse_trusted_proxies.cache_clear()


def test_behind_local_nginx_only_x_real_ip_counts(docker_gateway_172_18):
    # nginx на хосте -> контейнер видит docker-шлюз 172.18.0.1
    req = _req("172.18.0.1", {"CF-Connecting-IP": "185.71.76.1", "X-Forwarded-For": "185.71.76.1",
                              "X-Real-IP": "203.0.113.9"})
    assert api_main._get_client_ip(req) == "203.0.113.9"
    req = _req("172.18.0.1", {"X-Real-IP": "185.71.76.5"})
    assert api_main._is_yookassa_ip(api_main._get_client_ip(req))


def test_other_container_on_bridge_is_not_trusted(docker_gateway_172_18):
    # соседний контейнер (не шлюз) не может подставить X-Real-IP
    req = _req("172.18.0.5", {"X-Real-IP": "185.71.76.5"})
    assert api_main._get_client_ip(req) == "172.18.0.5"
    assert not api_main._is_yookassa_ip(api_main._get_client_ip(req))
    for peer in ("10.1.2.3", "192.168.1.10"):
        assert api_main._get_client_ip(_req(peer, {"X-Real-IP": "185.71.76.5"})) == peer


def test_localhost_is_trusted_without_docker(monkeypatch):
    api_main._parse_trusted_proxies.cache_clear()
    with patch.object(api_main, "_docker_default_gateway", return_value=None):
        assert api_main._get_client_ip(_req("127.0.0.1", {"X-Real-IP": "185.71.77.3"})) == "185.71.77.3"
        assert api_main._get_client_ip(_req("172.18.0.1", {"X-Real-IP": "185.71.77.3"})) == "172.18.0.1"
    api_main._parse_trusted_proxies.cache_clear()


def test_docker_gateway_from_proc_route(tmp_path):
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    )
    assert api_main._docker_default_gateway(str(route)) == "172.18.0.1"
    assert api_main._docker_default_gateway(str(tmp_path / "missing")) is None


@pytest.mark.parametrize("ip,ok", [
    ("77.75.156.11", True), ("77.75.156.35", True), ("77.75.156.12", False),
    ("185.71.76.31", True), ("185.71.76.32", False), ("2a02:5180::1", True),
])
def test_yookassa_allowlist_matches_official_list(ip, ok):
    assert api_main._is_yookassa_ip(ip) is ok


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
