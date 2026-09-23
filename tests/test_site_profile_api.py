"""Внутренний API для сайта: GET /internal/site/users/{id}/profile и /health.

БД подменяется снимком (load_db_snapshot), Redis — FakeRedis, панель Remnawave
(поиск main-юзера по telegramId, когда в БД нет связки) — на уровне HTTP через
httpx.MockTransport. Ответ /api/users/stream повторяет схему Remnawave 3.4.3
(все required-поля UserSchema из OpenAPI, снятого с @remnawave/backend-contract
3.4.3; см. docs РЕВЬЮ_БОТА_2026-09-23/impl/remnawave_3.4.3_openapi.json).
"""
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import main as api_main
from app.api import internal_site
from app.services import site_profile as sp
from tests.fakes.redis import FakeRedis

TOKEN = "t0ken-for-tests-0123456789abcdef"
HDR = {"X-Internal-Token": TOKEN}
SECRET_URL = "https://sub.example.com/SECRETTOKEN123"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def panel_user(uid: int, telegram_id: int, squads=("standard",)) -> dict:
    """Юзер панели в форме /api/users/stream Remnawave 3.4.3 (все required-поля)."""
    return {
        "id": uid,
        "shortUuid": "shortuuid0001",
        "username": f"tg_{telegram_id}",
        "status": "ACTIVE",
        "trafficLimitBytes": 0,
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": "2026-09-28T10:00:00.000Z",
        "telegramId": telegram_id,
        "email": None,
        "description": None,
        "tag": None,
        "hwidDeviceLimit": None,
        "externalSquadUuid": None,
        "trojanPassword": "trojan-secret",
        "vlessUuid": "0b3c0d6e-4a47-4b7a-9f39-5d2b1c0e9a11",
        "ssPassword": "ss-secret",
        "lastTriggeredThreshold": 0,
        "subRevokedAt": None,
        "lastTrafficResetAt": None,
        "createdAt": "2026-09-23T10:00:00.000Z",
        "updatedAt": "2026-09-23T10:00:00.000Z",
        "subscriptionUrl": SECRET_URL,
        "activeInternalSquads": [
            {"uuid": "9301b50f-6b90-47af-ac53-e5d69544f43f", "name": n} for n in squads
        ],
        "userTraffic": {
            "usedTrafficBytes": 0,
            "lifetimeUsedTrafficBytes": 0,
            "onlineAt": None,
            "firstConnectedAt": None,
            "lastConnectedNodeUuid": None,
        },
    }


class FakePanel:
    """HTTP-слой Remnawave: только GET /api/users/stream?telegramId=..."""

    def __init__(self):
        self.users = []
        self.down = False
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, dict(request.url.params)))
        assert request.method == "GET", "профиль сайта не должен ничего менять в панели"
        if self.down:
            return httpx.Response(502, json={"message": "Bad Gateway"})
        if request.url.path == "/api/users/stream":
            tg = request.url.params.get("telegramId")
            users = [u for u in self.users if str(u["telegramId"]) == str(tg)]
            return httpx.Response(200, json={"response": {"users": users, "nextCursor": None, "hasMore": False}})
        return httpx.Response(404, json={"message": "Not Found", "statusCode": 404})


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def tg_user(tid=111, remna_id="101"):
    return sp.DbUser(telegram_id=tid, username="name", first_name="Name",
                     created_at=dt("2025-12-01T10:00:00"), remna_user_id=remna_id)


def sub(id_, kind, plan, remna_id, active=True, cfg=None, updated="2026-09-01T10:00:00"):
    return sp.DbSubscription(id=id_, sub_kind=kind, plan_code=plan, remna_user_id=remna_id,
                             active=active, updated_at=dt(updated), config_data=cfg or {})


def pay(id_, created, status="succeeded", provider="yookassa", amount=249, meta=None, paid=None):
    return sp.DbPayment(id=id_, provider=provider, status=status, amount=amount,
                        created_at=dt(created), paid_at=dt(paid) if paid else None,
                        payment_metadata=meta or {})


@pytest.fixture
def env(monkeypatch):
    """Токен, FakeRedis, панель через MockTransport, БД через снимок."""
    from app.config import settings
    from app.remnawave import client as rc

    redis = FakeRedis()
    panel = FakePanel()
    state = {"snap": sp.DbSnapshot(user=None, subscriptions=[], payments=[]), "loads": 0,
             "last_plan": None}

    async def fake_load(telegram_id):
        state["loads"] += 1
        snap = state["snap"]
        if isinstance(snap, Exception):
            raise snap
        return snap

    async def fake_last_plan(telegram_id):
        return state["last_plan"]

    monkeypatch.setattr(settings, "BOT_INTERNAL_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "REMNA_API_BASE", "https://panel.example.com")
    monkeypatch.setattr(settings, "REMNA_API_KEY", "panel-api-key")
    monkeypatch.setattr(settings, "remna_base_url", None)
    monkeypatch.setattr(settings, "remna_api_token", None)
    monkeypatch.setattr(rc, "_shared_http_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(panel.handler)))
    monkeypatch.setattr(sp, "load_db_snapshot", fake_load)
    monkeypatch.setattr("app.services.users.get_user_last_plan", fake_last_plan)
    monkeypatch.setattr("app.services.cache.get_redis_client", lambda: redis)

    client = TestClient(api_main.app)
    yield client, state, panel, redis
    monkeypatch.setattr(rc, "_shared_http_client", None)


def get_profile(client, tid=111, headers=HDR):
    return client.get(f"/internal/site/users/{tid}/profile", headers=headers)


# ---------------------------------------------------------------------------
# auth, rate limit, health
# ---------------------------------------------------------------------------


def test_missing_token_403(env):
    client, *_ = env
    r = get_profile(client, headers={})
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden"}
    assert client.get("/internal/site/health").status_code == 403


def test_wrong_token_403(env):
    client, *_ = env
    r = get_profile(client, headers={"X-Internal-Token": TOKEN + "x"})
    assert r.status_code == 403


def test_empty_config_503(env, monkeypatch):
    from app.config import settings

    client, *_ = env
    monkeypatch.setattr(settings, "BOT_INTERNAL_TOKEN", "")
    assert get_profile(client).status_code == 503
    assert get_profile(client, headers={}).status_code == 503
    r = client.get("/internal/site/health", headers=HDR)
    assert r.status_code == 503 and r.json() == {"error": "disabled"}


def test_rate_limit_120_per_minute(env, monkeypatch):
    from types import SimpleNamespace

    client, state, _, _ = env
    # одно и то же минутное окно на весь тест
    monkeypatch.setattr(internal_site, "time", SimpleNamespace(time=lambda: 1_790_000_000.0))
    state["snap"] = sp.DbSnapshot(user=tg_user(), subscriptions=[], payments=[])
    codes = [client.get("/internal/site/health", headers=HDR).status_code for _ in range(120)]
    with patch.object(sp, "db_is_alive", AsyncMock(return_value=True)):
        assert all(c == 200 for c in codes)
        r = get_profile(client)
    assert r.status_code == 429 and r.json() == {"error": "rate_limited"}


def test_health(env):
    client, *_ = env
    with patch.object(sp, "db_is_alive", AsyncMock(return_value=True)):
        assert client.get("/internal/site/health", headers=HDR).json() == {"ok": True, "db": True}
    with patch.object(sp, "db_is_alive", AsyncMock(return_value=False)):
        assert client.get("/internal/site/health", headers=HDR).json() == {"ok": False, "db": False}


def test_db_down_503(env):
    client, state, _, _ = env
    state["snap"] = sp.ProfileDbUnavailable("boom")
    r = get_profile(client)
    assert r.status_code == 503 and r.json() == {"error": "db_unavailable"}


def test_existing_routes_untouched_and_internal_not_in_docs(env):
    client, *_ = env
    assert client.get("/openapi.json").status_code == 404


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------


def test_main_only(env):
    client, state, panel, _ = env
    state["snap"] = sp.DbSnapshot(
        user=tg_user(remna_id="101"),
        subscriptions=[sub(1, "main", "standard", "101", cfg={"subscription_url": SECRET_URL})],
        payments=[pay(1, "2026-09-01T10:00:00", meta={"plan_code": "standard", "period_months": "1"},
                      paid="2026-09-01T10:01:00")],
    )
    r = get_profile(client)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"user", "accounts", "payments", "stats"}
    assert body["user"] == {"telegram_id": 111, "username": "name", "first_name": "Name",
                            "customer_since": "2025-12-01T10:00:00Z"}
    assert body["accounts"] == [{
        "kind": "main", "remna_uuid": "101", "plan_code": "standard", "plan_title": "Standard",
        "legacy": False, "device_limit": 5,
    }]
    assert body["payments"][0]["description"] == "Standard, 1 мес"
    assert body["payments"][0]["period_months"] == 1
    # связка есть в БД: в панель не ходили; ссылка подписки из config_data не утекла
    assert panel.requests == []
    assert "SECRETTOKEN123" not in r.text and "subscription_url" not in r.text


def test_main_from_subscription_row_when_tg_row_has_no_remna_id(env):
    client, state, *_ = env
    state["snap"] = sp.DbSnapshot(
        user=tg_user(remna_id=None),
        subscriptions=[sub(1, "main", "premium", "77")],
        payments=[],
    )
    acc = get_profile(client).json()["accounts"]
    assert acc == [{"kind": "main", "remna_uuid": "77", "plan_code": "premium",
                    "plan_title": "Премиум тариф", "legacy": True, "device_limit": 15}]


def test_main_plus_obhod_with_package(env):
    client, state, *_ = env
    state["snap"] = sp.DbSnapshot(
        user=tg_user(remna_id="101"),
        subscriptions=[
            sub(1, "main", "pro", "101"),
            sub(2, "obhod", "obhod", "202", cfg={
                "subscription_url": SECRET_URL,
                "package": "obhod_250",
                "package_until": "2026-10-20T00:00:00",
                "package_limit_bytes": 268435456000,
            }),
        ],
        payments=[],
    )
    r = get_profile(client)
    acc = r.json()["accounts"]
    assert acc[0]["kind"] == "main" and acc[0]["plan_code"] == "pro" and acc[0]["device_limit"] == 10
    assert acc[1] == {
        "kind": "obhod", "remna_uuid": "202", "plan_code": "pro", "plan_title": "RU-вход",
        "legacy": False, "device_limit": 10,
        "package": {"code": "obhod_250", "until": "2026-10-20T00:00:00Z", "limit_bytes": 268435456000},
    }
    assert "SECRETTOKEN123" not in r.text


def test_obhod_without_package_is_null(env):
    client, state, *_ = env
    state["snap"] = sp.DbSnapshot(
        user=tg_user(remna_id="101"),
        subscriptions=[sub(1, "main", "pro", "101"), sub(2, "obhod", "obhod", "202", active=False)],
        payments=[],
    )
    acc = get_profile(client).json()["accounts"]
    assert acc[1]["kind"] == "obhod" and acc[1]["package"] is None


def test_plan_from_last_payment_when_no_row(env):
    client, state, *_ = env
    state["snap"] = sp.DbSnapshot(user=tg_user(remna_id="101"), subscriptions=[], payments=[])
    state["last_plan"] = "lite"
    acc = get_profile(client).json()["accounts"]
    assert acc == [{"kind": "main", "remna_uuid": "101", "plan_code": "lite", "plan_title": "Lite",
                    "legacy": False, "device_limit": 2}]


def test_trial_without_db_row_found_in_panel(env):
    client, state, panel, redis = env
    state["snap"] = sp.DbSnapshot(user=tg_user(remna_id=None), subscriptions=[], payments=[])
    panel.users = [panel_user(555, 111), panel_user(556, 999)]
    r = get_profile(client)
    acc = r.json()["accounts"]
    assert acc == [{"kind": "main", "remna_uuid": "555", "plan_code": None, "plan_title": None,
                    "legacy": False, "device_limit": None}]
    assert panel.requests == [("GET", "/api/users/stream", {"telegramId": "111", "size": "25"})]
    # из панели в ответ не попало ничего, кроме id
    for secret in ("SECRETTOKEN123", "trojan-secret", "ss-secret", "shortuuid0001"):
        assert secret not in r.text
    assert "site:profile:111" in redis.store


def test_trial_lookup_panel_down_item_omitted_and_not_cached(env):
    client, state, panel, redis = env
    state["snap"] = sp.DbSnapshot(user=tg_user(remna_id=None), subscriptions=[], payments=[])
    panel.down = True
    r = get_profile(client)
    assert r.status_code == 200
    assert r.json()["accounts"] == []
    assert "site:profile:111" not in redis.store


def test_no_subscriptions_at_all(env):
    client, state, panel, _ = env
    state["snap"] = sp.DbSnapshot(
        user=tg_user(remna_id=None), subscriptions=[],
        payments=[pay(5, "2026-09-02T10:00:00", status="canceled", meta={"plan_code": "lite", "period_months": "3"})],
    )
    body = get_profile(client).json()
    assert body["accounts"] == []
    assert [p["id"] for p in body["payments"]] == [5]
    assert body["payments"][0]["status"] == "canceled"
    assert body["stats"] == {"payments_count": 0, "paid_total_rub": 0.0,
                             "first_payment_at": None, "last_payment_at": None}


def test_unknown_id_404(env):
    client, state, panel, _ = env
    state["snap"] = sp.DbSnapshot(user=None, subscriptions=[], payments=[])
    r = get_profile(client, tid=424242)
    assert r.status_code == 404 and r.json() == {"error": "not_found"}
    assert panel.requests == []
    assert get_profile(client, tid=0).status_code == 404


# ---------------------------------------------------------------------------
# payments + stats
# ---------------------------------------------------------------------------


def test_payments_order_kind_and_stats(env):
    client, state, *_ = env
    payments = [
        pay(1, "2025-12-01T10:00:00", amount=99, meta={"plan_code": "basic", "period_months": "1"},
            paid="2025-12-01T10:01:00"),
        pay(4, "2026-09-01T10:00:00", amount=599, meta={"plan_code": "obhod_250", "period_months": "1"},
            paid="2026-09-01T10:02:00"),
        pay(2, "2026-06-01T10:00:00", provider="promo", amount=0,
            meta={"promo_code": "sun718", "tariff": "sun718_5d"}, paid="2026-06-01T10:00:00"),
        pay(3, "2026-08-01T10:00:00", status="canceled", amount=449,
            meta={"plan_code": "pro", "period_months": "1"}),
        pay(5, "2026-09-10T10:00:00", status="pending", amount=249,
            meta={"plan_code": "standard", "period_months": "1"}),
        pay(6, "2026-07-01T10:00:00", status="refunded", amount=129,
            meta={"plan_code": "lite", "period_months": "1"}, paid="2026-07-01T10:00:00"),
        pay(7, "2026-07-15T10:00:00", amount=649, meta={"plan_code": "standard", "period_months": "3"}),
        pay(8, "2026-05-01T10:00:00", amount=100, meta={}, paid="2026-05-01T10:00:00"),
    ]
    state["snap"] = sp.DbSnapshot(user=tg_user(), subscriptions=[], payments=payments)
    body = get_profile(client).json()
    got = body["payments"]
    assert [p["id"] for p in got] == [5, 4, 3, 7, 6, 2, 8, 1]
    kinds = {p["id"]: p["kind"] for p in got}
    assert kinds == {1: "subscription", 2: "promo", 3: "subscription", 4: "obhod_package",
                     5: "subscription", 6: "subscription", 7: "subscription", 8: "subscription"}
    by_id = {p["id"]: p for p in got}
    assert by_id[4]["description"] == "RU-вход: пакет 250 ГБ, 1 мес"
    assert by_id[2]["description"] == "Промокод sun718: Pro, 5 дн"
    assert by_id[2]["plan_code"] is None and by_id[2]["period_months"] is None
    assert by_id[1]["description"] == "Базовый тариф, 1 мес"
    assert by_id[7]["description"] == "Standard, 3 мес"
    assert by_id[8]["description"] == "Оплата подписки" and by_id[8]["plan_code"] is None
    assert by_id[3]["paid_at"] is None and by_id[3]["amount_rub"] == 449.0
    assert all("ё" not in p["description"] for p in got)
    # stats: succeeded и не промо -> 1, 4, 7, 8
    assert body["stats"] == {
        "payments_count": 4,
        "paid_total_rub": 99 + 599 + 649 + 100.0,
        "first_payment_at": "2025-12-01T10:01:00Z",
        "last_payment_at": "2026-09-01T10:02:00Z",
    }


def test_payments_limited_to_100_but_stats_over_all(env):
    client, state, *_ = env
    payments = [pay(i, f"2026-01-01T10:{i // 60:02d}:{i % 60:02d}", amount=10,
                    meta={"plan_code": "lite", "period_months": "1"}) for i in range(1, 131)]
    state["snap"] = sp.DbSnapshot(user=tg_user(), subscriptions=[], payments=payments)
    body = get_profile(client).json()
    assert len(body["payments"]) == 100
    assert body["payments"][0]["id"] == 130 and body["payments"][-1]["id"] == 31
    assert body["stats"]["payments_count"] == 130 and body["stats"]["paid_total_rub"] == 1300.0


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


def test_cache_hit_and_drop_after_payment(env):
    client, state, _, redis = env
    state["snap"] = sp.DbSnapshot(user=tg_user(), subscriptions=[sub(1, "main", "lite", "101")], payments=[])
    first = get_profile(client).json()
    assert state["loads"] == 1
    # БД поменялась, но 60 с отдаем кэш
    state["snap"] = sp.DbSnapshot(user=tg_user(), subscriptions=[sub(1, "main", "pro", "101")], payments=[])
    assert get_profile(client).json() == first
    assert state["loads"] == 1

    # успешная оплата / выдача зовут invalidate_sync_cache -> профиль сброшен
    import asyncio

    from app.services.cache import invalidate_sync_cache
    asyncio.run(invalidate_sync_cache(111))
    assert "site:profile:111" not in redis.store
    assert get_profile(client).json()["accounts"][0]["plan_code"] == "pro"
    assert state["loads"] == 2


def test_cache_key_ttl_60(env, monkeypatch):
    client, state, _, redis = env
    calls = []
    orig = redis.set

    async def spy(key, value, ex=None, nx=False):
        calls.append((key, ex))
        return await orig(key, value, ex=ex, nx=nx)

    monkeypatch.setattr(redis, "set", spy)
    state["snap"] = sp.DbSnapshot(user=tg_user(), subscriptions=[], payments=[])
    get_profile(client)
    assert ("site:profile:111", 60) in calls


def test_obhod_package_payment_drops_profile_cache():
    """Ветка оплаты пакета обхода не доходит до общего invalidate_sync_cache."""
    import inspect

    from app.services.payments import yookassa

    src = inspect.getsource(yookassa.handle_successful_payment)
    pkg_branch = src[src.index("is_obhod_package_code(plan_code)"):src.index("obhod package paid but NOT applied")]
    assert "invalidate_site_profile_cache" in pkg_branch


def test_router_module_is_thin():
    """Логика профиля в services/site_profile.py, в маршрутах нет SQL и панели."""
    import inspect

    src = inspect.getsource(internal_site)
    for forbidden in ("select(", "SessionLocal", "RemnaClient", "Payment"):
        assert forbidden not in src
