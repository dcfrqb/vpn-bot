"""Release 3.0 invariants (plan section 0.6). Every stream must keep these green.

1. Manual squads (*-m, *-friend, arcadia) are never written by the bot.
2. hwidDeviceLimit is never lowered.
3. Price only from app.domain.plans (callbacks carry no money; old pay buttons
   ignore the amount; no catalog price literals in the bot layer).
4. Queries on subscriptions in 3.0 code keep the sub_kind filter.
5. No raw exception text to users from 3.0 handlers.
6. No subscription URLs in logs.
7. No letter U+0451 in 3.0 prose (code, texts, docs).
"""
import ast
import itertools
import pathlib
import re

import pytest

from app.domain.plans import OBHOD_SQUAD_NAME, PLAN_CATALOG, OBHOD_PACKAGE_CATALOG
from app.services.remna_tariff import (
    apply_tariff_to_remna_user,
    is_manual_squad_name,
    managed_tariff_squad_names,
    resolve_device_limit,
)
from tests.fakes.remnawave import FakeRemna, FakeRemnaGateway

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "app"

# 3.0 code (Foundation + streams). Streams add their new top-level modules here.
NEW_LAYER = [
    SRC / "bot", SRC / "domain", SRC / "worker", SRC / "infra", SRC / "api" / "routes",
    SRC / "container.py", SRC / "services" / "ports.py", SRC / "services" / "shims.py",
    SRC / "services" / "notifications.py",
    # stream C
    SRC / "services" / "events_repo.py", SRC / "services" / "grace.py", SRC / "services" / "maintenance.py",
]
MANUAL = ["pro-m", "lite-m", "standard-m", "premium-m", "pro-friend", "premium-friend", "arcadia"]


def _py_files(paths):
    for p in paths:
        if p.is_dir():
            yield from sorted(p.rglob("*.py"))
        elif p.exists():
            yield p


# ---------------------------------------------------------------- 1. manual squads

def test_catalog_never_contains_manual_squads():
    assert not any(is_manual_squad_name(m["squad"]) for m in PLAN_CATALOG.values())
    assert not is_manual_squad_name(OBHOD_SQUAD_NAME)
    assert not (managed_tariff_squad_names() & set(MANUAL))
    for name in MANUAL:
        assert is_manual_squad_name(name)


@pytest.mark.parametrize("plan", sorted(c for c, m in PLAN_CATALOG.items() if m["squad"]))
@pytest.mark.parametrize("manual", [[], ["pro-m"], ["arcadia", "premium-friend"], ["lite-m", "us-2"]])
async def test_apply_tariff_keeps_manual_squads(plan, manual):
    fake = FakeRemna()
    fake.add_user(1, "u", squads=["lite", *manual], limit=None)
    await apply_tariff_to_remna_user(fake, "1", plan, expire_at="2026-12-01T00:00:00Z")
    after = set(fake.squad_names(1))
    assert set(manual) <= after, "manual squad removed"
    added = after - {"lite", *manual}
    assert not any(is_manual_squad_name(n) for n in added), "manual squad added"


async def test_gateway_refuses_to_write_manual_squads():
    gw = FakeRemnaGateway()
    gw.fake.add_user(1, "u", squads=["pro-m"], limit=3)
    for name in MANUAL:
        with pytest.raises(ValueError):
            await gw.update_user(1, squads=[name])
    await gw.update_user(1, squads=["standard"])
    assert set(gw.fake.squad_names(1)) == {"standard", "pro-m"}


# ---------------------------------------------------------------- 2. device limit

@pytest.mark.parametrize("current,plan_limit,foreign", itertools.product(
    [None, 0, 1, 2, 5, 10, 15, 50, "7", "x"], [2, 5, 10, 15], [False, True]))
def test_resolve_device_limit_never_lowers(current, plan_limit, foreign):
    new = resolve_device_limit(current, plan_limit, has_foreign_squads=foreign)
    try:
        cur = int(current) if current is not None else None
    except ValueError:
        cur = "bad"
    if new is not None and cur not in (None, "bad"):
        assert new >= cur
    if cur == 0:
        assert new is None  # 0 = unlimited, never touched


@pytest.mark.parametrize("plan", ["lite", "standard", "pro", "basic", "premium", "trial"])
async def test_apply_tariff_never_lowers_limit(plan):
    fake = FakeRemna()
    fake.add_user(1, "u", squads=["premium"], limit=15)
    await apply_tariff_to_remna_user(fake, "1", plan, expire_at="2026-12-01T00:00:00Z")
    assert fake.users[1]["hwidDeviceLimit"] >= 15


async def test_gateway_never_lowers_limit():
    gw = FakeRemnaGateway()
    gw.fake.add_user(1, "u", squads=[], limit=10)
    gw.fake.add_user(2, "v", squads=[], limit=0)
    await gw.update_user(1, device_limit=2)
    await gw.update_user(2, device_limit=5)
    assert gw.fake.users[1]["hwidDeviceLimit"] == 10 and gw.fake.users[2]["hwidDeviceLimit"] == 0
    assert not gw.fake.patches


# ---------------------------------------------------------------- 3. price only from plans

def test_callbacks_carry_no_money_and_old_amount_is_ignored():
    from app.bot import callbacks as cb
    from app.bot.legacy_aliases import rewrite

    for c in cb.ALL_CALLBACKS:
        assert not {"amount", "price", "sum", "rub"} & set(c.model_fields), c.__name__
    assert rewrite("pay_yookassa_pro_1_1") == cb.Period(c="pro", m=1).pack()


def test_no_catalog_price_literals_in_bot_layer():
    prices = {p for m in PLAN_CATALOG.values() for p in m["prices"].values() if p >= 99}
    prices |= {m["price"] for m in OBHOD_PACKAGE_CATALOG.values() if m["price"] >= 99}
    hits = []
    for f in _py_files([SRC / "bot", SRC / "domain" / "texts", SRC / "worker"]):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, int) and node.value in prices:
                hits.append(f"{f.relative_to(ROOT)}:{node.lineno}: {node.value}")
    assert not hits, "prices must come from app.domain.plans:\n" + "\n".join(hits)


# ---------------------------------------------------------------- 4. sub_kind filter

def test_subscription_queries_in_new_layer_filter_sub_kind():
    offenders = []
    for f in _py_files(NEW_LAYER):
        text = f.read_text()
        if re.search(r"select\(\s*(Subscription|DbSubscription)\b", text) and "sub_kind" not in text:
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, offenders


# ---------------------------------------------------------------- 5. no exception text to users

USER_SENDERS = {"answer", "edit_text", "send_message", "reply", "edit_caption", "answer_callback_query"}


def test_no_exception_text_sent_to_users_from_new_layer():
    offenders = []
    for f in _py_files([SRC / "bot"]):
        for handler in (n for n in ast.walk(ast.parse(f.read_text())) if isinstance(n, ast.ExceptHandler)):
            if not handler.name:
                continue
            for call in (n for n in ast.walk(handler) if isinstance(n, ast.Call)):
                if isinstance(call.func, ast.Attribute) and call.func.attr in USER_SENDERS:
                    names = {n.id for a in [*call.args, *[k.value for k in call.keywords]] for n in ast.walk(a)
                             if isinstance(n, ast.Name)}
                    if handler.name in names:
                        offenders.append(f"{f.relative_to(ROOT)}:{call.lineno}")
    assert not offenders, offenders


# ---------------------------------------------------------------- 6. no subscription URLs in logs

URL_NAMES = ("subscription_url", "sub_url", "subscriptionurl", "suburl", "subscription_link", "sub_link")


def test_no_subscription_url_interpolated_into_logs():
    hits = []
    for f in _py_files([SRC]):
        for n in ast.walk(ast.parse(f.read_text())):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "logger"):
                for sub in (s for a in n.args for s in ast.walk(a) if isinstance(s, ast.FormattedValue)):
                    src = ast.unparse(sub.value).lower()
                    if any(u in src for u in URL_NAMES):
                        hits.append(f"{f.relative_to(ROOT)}:{n.lineno}: {src}")
    assert not hits, hits


async def test_client_does_not_log_the_url_at_runtime(monkeypatch):
    from app.logger import logger
    from app.remnawave.client import RemnaClient

    token = "SUPERSECRETTOKEN0123456789"
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        monkeypatch.setattr("app.config.settings.SUBSCRIPTION_BASE_URL", "https://sub.example.com")

        async def by_id(self, uid):
            return {"response": {"subscriptionUrl": f"https://panel.example/{token}", "subscriptionToken": token}}

        monkeypatch.setattr(RemnaClient, "get_user_by_id", by_id)
        url = await RemnaClient().get_user_subscription_url("1")

        async def by_id_token_only(self, uid):
            return {"response": {"subscriptionToken": token}}

        monkeypatch.setattr(RemnaClient, "get_user_by_id", by_id_token_only)
        url2 = await RemnaClient().get_user_subscription_url("1")
    finally:
        logger.remove(sink)
    assert token in url and token in url2
    assert not [l for l in lines if token in l]


# ---------------------------------------------------------------- 7. no yo letter

def test_no_yo_letter_in_new_layer_and_docs():
    offenders = []
    files = list(_py_files(NEW_LAYER)) + [
        SRC / "db" / "migrations" / "versions" / "r30_01_additive.py",
        ROOT / "docs" / "ARCHITECTURE_3.0.md",
        *sorted((ROOT / "tests").rglob("test_r3_*.py")),
        *sorted((ROOT / "tests" / "flows").rglob("*.py")),
    ]
    for f in files:
        if f.exists() and ("ё" in f.read_text() or "Ё" in f.read_text()):
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, offenders
