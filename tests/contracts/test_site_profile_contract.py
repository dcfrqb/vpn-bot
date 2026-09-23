"""Contract snapshot: GET /internal/site/users/{id}/profile (site <- bot).

The site (vpn.crs-projects.com) parses this JSON; 3.0 must not change it.
Same fixtures as tests/test_site_profile_api.py (DB snapshot, FakeRedis,
Remnawave over httpx.MockTransport)."""
from app.services import site_profile as sp
from tests.contracts.golden import assert_golden
from tests.test_site_profile_api import (
    SECRET_URL,
    get_profile,
    panel_user,
    pay,
    sub,
    tg_user,
)


def test_profile_contract_rich_user(env):
    client, state, panel, _ = env
    state["snap"] = sp.DbSnapshot(
        user=tg_user(tid=111, remna_id="101"),
        subscriptions=[
            sub(1, "main", "pro", "101", cfg={"subscription_url": SECRET_URL}),
            sub(2, "obhod", "obhod", "102", cfg={"subscription_url": SECRET_URL + "2", "package": "obhod_250",
                                                "package_until": "2026-10-20T00:00:00",
                                                "package_limit_bytes": 268435456000}),
            sub(3, "main", "lite", "99", active=False, updated="2026-05-01T10:00:00"),
        ],
        payments=[
            pay(1, "2026-09-01T10:00:00", amount=449, meta={"plan_code": "pro", "period_months": "1"},
                paid="2026-09-01T10:01:00"),
            pay(2, "2026-08-01T10:00:00", provider="promo", amount=0,
                meta={"promo_code": "trial", "tariff": "trial_standard_5d"}),
            pay(3, "2026-07-01T10:00:00", status="canceled", amount=249,
                meta={"plan_code": "standard", "period_months": "1"}),
            pay(4, "2026-09-10T10:00:00", amount=599, meta={"plan_code": "obhod_250", "period_months": "1"},
                paid="2026-09-10T10:02:00"),
        ],
    )
    state["last_plan"] = "pro"
    panel.users = [panel_user(101, 111, squads=("pro",)), panel_user(102, 111, squads=("obhod",))]
    r = get_profile(client)
    assert r.status_code == 200
    body = r.json()
    assert "SECRET" not in r.text
    assert_golden("site_profile_rich", body)


def test_profile_contract_no_panel_link(env):
    client, state, panel, _ = env
    state["snap"] = sp.DbSnapshot(user=tg_user(tid=111, remna_id=None), subscriptions=[], payments=[])
    r = get_profile(client)
    assert_golden("site_profile_empty", {"status": r.status_code, "body": r.json()})


def test_profile_contract_errors(env):
    client, state, _, _ = env
    out = {}
    out["forbidden"] = [client.get("/internal/site/users/111/profile").status_code,
                        client.get("/internal/site/users/111/profile").json()]
    state["snap"] = sp.DbSnapshot(user=None, subscriptions=[], payments=[])
    r = get_profile(client)
    out["not_found"] = [r.status_code, r.json()]
    state["snap"] = sp.ProfileDbUnavailable("x")
    r = get_profile(client)
    out["db_unavailable"] = [r.status_code, r.json()]
    assert_golden("site_profile_errors", out)
