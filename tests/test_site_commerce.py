"""Website-only money/provisioning tests. No live providers or panel writes."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.domain.models import PanelUser
from app.services.site_commerce import (
    SiteCommerce,
    SiteCommerceError,
    handle_site_notification,
)

NOW = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
ACCOUNT = str(uuid4())


class Store:
    def __init__(self):
        self.customers, self.data, self.locks = {}, {}, {}

    @asynccontextmanager
    async def lock(self, key):
        async with self.locks.setdefault(str(key), asyncio.Lock()):
            yield

    async def customer(self, key):
        return deepcopy(self.customers.get(str(key)))

    async def save_customer(self, data):
        self.customers[data["account_id"]] = deepcopy(data)

    async def order(self, key):
        return deepcopy(self.data.get(str(key)))

    async def save_order(self, data):
        self.data[data["id"]] = deepcopy(data)

    async def orders(self, account):
        return sorted(
            [deepcopy(o) for o in self.data.values() if o["account_id"] == account],
            key=lambda o: o["created_at"],
            reverse=True,
        )

    async def external(self, key):
        return next(
            (deepcopy(o) for o in self.data.values() if o.get("provider_id") == key),
            None,
        )

    async def recovery(self):
        return [deepcopy(o) for o in self.data.values()]


class Provider:
    def __init__(self):
        self.data, self.keys, self.calls = {}, {}, 0
        self.fail_after_create = False

    async def create_payment(self, body, key):
        self.calls += 1
        if key not in self.keys:
            pid = str(uuid4())
            self.keys[key] = pid
            self.data[pid] = {
                **deepcopy(body),
                "id": pid,
                "test": True,
                "paid": False,
                "status": "pending",
                "recipient": {"account_id": "shop"},
                "confirmation": {
                    "confirmation_url": "https://yoomoney.ru/checkout/test"
                },
            }
        if self.fail_after_create:
            self.fail_after_create = False
            raise OSError("response lost")
        return deepcopy(self.data[self.keys[key]])

    async def get_payment(self, key):
        return deepcopy(self.data.get(key))


class Panel:
    def __init__(self):
        self.users, self.writes = {}, 0
        self.fail_after_create = False
        self.fail_obhod = False

    async def get_user(self, key):
        return self.users.get(int(key))

    async def find_user_by_username(self, name):
        return next((u for u in self.users.values() if u.username == name), None)

    async def create_user(self, username, **kw):
        assert kw["telegram_id"] is None
        assert len(username) <= 36
        if self.fail_obhod and username.startswith("wo_"):
            raise OSError("panel offline")
        key = len(self.users) + 1
        self.users[key] = PanelUser(
            id=key,
            username=username,
            status="ACTIVE",
            expire_at=kw["expire_at"],
            squads=tuple(kw["squads"]),
            device_limit=kw["device_limit"],
            traffic_limit_bytes=kw["traffic_limit_bytes"],
            traffic_limit_strategy=kw["traffic_limit_strategy"],
            subscription_url="https://sub.example.com/private",
            raw={"description": kw["description"]},
        )
        self.writes += 1
        if self.fail_after_create:
            self.fail_after_create = False
            raise OSError("create response lost")
        return self.users[key]

    async def update_user(self, key, **kw):
        current = self.users[key]
        self.users[key] = replace(
            current,
            expire_at=kw.get("expire_at", current.expire_at),
            squads=tuple(kw.get("squads", current.squads)),
            device_limit=max(kw.get("device_limit", 0), current.device_limit or 0),
        )
        self.writes += 1
        return self.users[key]

    async def disable_user(self, key):
        self.users[key] = replace(self.users[key], status="DISABLED")

    async def enable_user(self, key):
        self.users[key] = replace(self.users[key], status="ACTIVE")


def service():
    settings = SimpleNamespace(
        SITE_CHECKOUT_ENABLED=True,
        SITE_COMMERCE_RECOVERY_ENABLED=True,
        SITE_PAYMENT_TEST_MODE=True,
        SITE_PUBLIC_ORIGIN="https://vpn.example.com",
        YOOKASSA_SHOP_ID="shop",
        YOOKASSA_API_KEY="test_key",
    )
    return SiteCommerce(
        SimpleNamespace(settings=settings),
        store=Store(),
        provider=Provider(),
        panel=Panel(),
        clock=lambda: NOW,
    )


async def start(s, code="lite", months=1, order=None):
    return await s.start(ACCOUNT, None, code, months, order or str(uuid4()))


def pay(s, order):
    row = s.store.data[order["id"]]
    s.provider.data[row["provider_id"]].update(status="succeeded", paid=True)


async def test_web_purchase_no_fake_telegram_id_and_exactly_once():
    s = service()
    order = await start(s)
    customer = await s.store.customer(ACCOUNT)
    assert customer["backend"] == "web" and customer["telegram_id"] is None
    raw = next(iter(s.provider.data.values()))
    assert "tg_user_id" not in raw["metadata"]
    assert raw["metadata"]["site_account_id"] == ACCOUNT
    assert raw["confirmation"]
    pay(s, order)
    assert (await s.refresh(ACCOUNT, order["id"]))["state"] == "fulfilled"
    expiry = s.panel.users[1].expire_at
    assert expiry == datetime(2026, 12, 13, 12, tzinfo=timezone.utc)
    await s.refresh(ACCOUNT, order["id"])
    await handle_site_notification(
        {"event": "payment.succeeded", "object": raw}, service=s
    )
    assert s.panel.writes == 1 and s.panel.users[1].expire_at == expiry


async def test_pending_reuse_and_cross_account_access():
    s = service()
    first = await start(s)
    assert (await start(s))["id"] == first["id"]
    assert len(s.provider.data) == 1
    with pytest.raises(SiteCommerceError) as exc:
        await s.refresh(str(uuid4()), first["id"])
    assert exc.value.status == 404
    with pytest.raises(SiteCommerceError) as exc:
        await start(s, "pro")
    assert exc.value.code == "order_in_progress"


async def test_unknown_create_response_reuses_provider_key():
    s = service()
    s.provider.fail_after_create = True
    key = str(uuid4())
    with pytest.raises(OSError):
        await start(s, order=key)
    assert len(s.provider.data) == 1
    assert s.store.data[key]["state"] == "creating"
    await start(s, order=key)
    assert len(s.provider.data) == 1 and s.store.data[key]["state"] == "pending"


async def test_webhook_recovers_provider_id_after_lost_create_response():
    s = service()
    s.provider.fail_after_create = True
    key = str(uuid4())
    with pytest.raises(OSError):
        await start(s, order=key)
    raw = next(iter(s.provider.data.values()))
    s.provider.data[raw["id"]].update(status="succeeded", paid=True)
    await handle_site_notification(
        {"event": "payment.succeeded", "object": raw}, service=s
    )
    assert s.store.data[key]["state"] == "fulfilled"
    assert len(s.panel.users) == 1


async def test_panel_create_response_loss_recovered_without_duplicate_or_extra_month():
    s = service()
    order = await start(s)
    pay(s, order)
    s.panel.fail_after_create = True
    assert (await s.refresh(ACCOUNT, order["id"]))["state"] == "provisioning"
    assert len(s.panel.users) == 1
    target = s.store.data[order["id"]]["target_expire"]
    s.clock = lambda: NOW + timedelta(days=1)
    await s.recover()
    assert s.store.data[order["id"]]["state"] == "fulfilled"
    assert len(s.panel.users) == 1
    assert s.panel.users[1].expire_at.isoformat() == target


async def test_restart_pro_obhod_retry_keeps_target_and_renewal_stacks_once():
    s = service()
    order = await start(s, "pro")
    pay(s, order)
    s.panel.fail_obhod = True
    assert (await s.refresh(ACCOUNT, order["id"]))["state"] == "provisioning"
    target = s.panel.users[1].expire_at
    restarted = SiteCommerce(
        s.container,
        store=s.store,
        provider=s.provider,
        panel=s.panel,
        clock=lambda: NOW,
    )
    s.panel.fail_obhod = False
    await restarted.recover()
    assert len(s.panel.users) == 2
    assert all(u.expire_at == target for u in s.panel.users.values())
    assert s.panel.users[2].traffic_limit_bytes == 150 * 1024**3
    second = await start(s, "pro")
    pay(s, second)
    await s.refresh(ACCOUNT, second["id"])
    assert s.panel.users[1].expire_at == datetime(2027, 1, 13, 12, tzinfo=timezone.utc)
    await s.refresh(ACCOUNT, order["id"])
    assert s.panel.users[1].expire_at == datetime(2027, 1, 13, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", {"value": "1.00", "currency": "RUB"}),
        ("amount", {"value": "129.00", "currency": "USD"}),
        ("test", False),
        ("recipient", {"account_id": "another-shop"}),
        ("metadata", {"site_order_id": "another", "site_account_id": ACCOUNT}),
    ],
)
async def test_provider_mismatch_never_grants(field, value):
    s = service()
    order = await start(s)
    pay(s, order)
    raw = next(iter(s.provider.data.values()))
    raw[field] = value
    with pytest.raises(SiteCommerceError):
        await s.refresh(ACCOUNT, order["id"])
    assert not s.panel.users


async def test_webhook_body_not_trusted_and_wrong_lookup_cannot_bind():
    s = service()
    order = await start(s)
    raw = next(iter(s.provider.data.values()))
    await handle_site_notification(
        {
            "event": "payment.succeeded",
            "object": {**raw, "paid": True, "status": "succeeded"},
        },
        service=s,
    )
    assert not s.panel.users
    assert s.store.data[order["id"]]["state"] == "pending"


async def test_concurrent_start_and_fulfillment():
    s = service()
    a, b = await asyncio.gather(start(s), start(s))
    assert a["id"] == b["id"] and len(s.provider.data) == 1
    pay(s, a)
    await asyncio.gather(s.refresh(ACCOUNT, a["id"]), s.refresh(ACCOUNT, a["id"]))
    assert len(s.panel.users) == 1 and s.panel.writes == 1


async def test_full_refund_once_preserves_later_purchased_time():
    s = service()
    first = await start(s)
    pay(s, first)
    await s.refresh(ACCOUNT, first["id"])
    second = await start(s)
    pay(s, second)
    await s.refresh(ACCOUNT, second["id"])
    raw = s.provider.data[s.store.data[first["id"]]["provider_id"]]
    raw["refunded_amount"] = {"value": "129.00", "currency": "RUB"}
    assert (await s.refresh(ACCOUNT, first["id"]))["state"] == "refunded"
    target = s.panel.users[1].expire_at
    assert target == datetime(2026, 12, 14, 12, tzinfo=timezone.utc)
    await s.refresh(ACCOUNT, first["id"])
    assert s.panel.users[1].expire_at == target


async def test_foreign_username_and_disabled_user_never_adopted_or_enabled():
    s = service()
    order = await start(s)
    username = "w_" + ACCOUNT.replace("-", "")
    s.panel.users[1] = PanelUser(
        id=1,
        username=username,
        status="ACTIVE",
        expire_at=NOW,
        raw={"description": "owned by someone else"},
    )
    pay(s, order)
    assert (await s.refresh(ACCOUNT, order["id"]))["state"] == "held"
    assert s.panel.writes == 0


@pytest.mark.parametrize(
    "plan,months", [("trial", 1), ("lite", 2), ("basic", 1), ("pro", 0)]
)
async def test_invalid_quote(plan, months):
    s = service()
    with pytest.raises(SiteCommerceError):
        await start(s, plan, months)
    assert not s.provider.data


async def test_live_key_refused_in_test_mode():
    s = service()
    s.settings.YOOKASSA_API_KEY = "live_key"
    with pytest.raises(SiteCommerceError):
        await start(s)
    assert not s.provider.data


async def test_stale_price_and_cancelled_order_never_grant():
    s = service()
    with pytest.raises(SiteCommerceError) as exc:
        await s.start(ACCOUNT, None, "lite", 1, uuid4(), 1)
    assert exc.value.code == "price_changed" and not s.provider.data
    order = await start(s)
    next(iter(s.provider.data.values()))["status"] = "canceled"
    assert (await s.refresh(ACCOUNT, order["id"]))["state"] == "canceled"
    assert not s.panel.users
    assert (await start(s))["id"] != order["id"]


async def test_partial_refund_and_disabled_subscription_require_review():
    s = service()
    order = await start(s)
    pay(s, order)
    await s.refresh(ACCOUNT, order["id"])
    expiry = s.panel.users[1].expire_at
    next(iter(s.provider.data.values()))["refunded_amount"] = {
        "value": "1.00",
        "currency": "RUB",
    }
    assert (await s.refresh(ACCOUNT, order["id"]))["state"] == "held"
    assert s.panel.users[1].expire_at == expiry
    with pytest.raises(SiteCommerceError) as exc:
        await start(s)
    assert exc.value.code == "order_in_progress"
    s.store.data[order["id"]]["state"] = (
        "refunded"  # administrator resolved the held refund
    )
    s.panel.users[1] = replace(s.panel.users[1], status="DISABLED")
    second = await start(s)
    pay(s, second)
    assert (await s.refresh(ACCOUNT, second["id"]))["state"] == "held"
    assert (
        s.panel.users[1].status == "DISABLED" and s.panel.users[1].expire_at == expiry
    )


async def test_pro_downgrade_and_reenable_only_site_disabled_obhod():
    s = service()
    for code in ["pro", "lite", "pro"]:
        order = await start(s, code)
        pay(s, order)
        await s.refresh(ACCOUNT, order["id"])
        assert s.panel.users[2].status == ("ACTIVE" if code == "pro" else "DISABLED")
    assert len(s.panel.users) == 2
    assert s.panel.users[1].expire_at == datetime(2027, 2, 13, 12, tzinfo=timezone.utc)


async def test_telegram_site_order_delegates_to_existing_money_engine(monkeypatch):
    from tests.money.fakes import make_money
    from app.services import money as module

    money, deps = make_money()
    original_create = deps.payments.create_payment

    async def create(*args, **kwargs):
        intent = await original_create(*args, **kwargs)
        return replace(intent, confirmation_url="https://yoomoney.ru/checkout/test")

    monkeypatch.setattr(deps.payments, "create_payment", create)
    monkeypatch.setattr(module, "money", lambda _: money)
    s = service()
    key = str(uuid4())
    order = await s.start(ACCOUNT, 700002, "lite", 1, key, 129)
    row = await s.store.order(key)
    payment = await deps.store.get(row["telegram_payment_id"])
    assert payment.telegram_id == 700002 and payment.meta["site_order_id"] == key
    assert (await s.start(ACCOUNT, 700002, "lite", 1, key, 129))["id"] == order["id"]
    assert not s.panel.users and len(deps.payments.payments) == 1
    assert (
        await handle_site_notification(
            {
                "event": "refund.succeeded",
                "object": {"id": "refund", "payment_id": row["provider_id"]},
            },
            service=s,
        )
        is None
    )
    with pytest.raises(SiteCommerceError):
        await s.quote(ACCOUNT, 42, "lite", 1)


async def test_refund_of_latest_plan_restores_previous_plan_without_extra_days():
    s = service()
    first = await start(s, "pro")
    pay(s, first)
    await s.refresh(ACCOUNT, first["id"])
    second = await start(s, "lite")
    pay(s, second)
    await s.refresh(ACCOUNT, second["id"])
    assert s.panel.users[2].status == "DISABLED"
    raw = s.provider.data[s.store.data[second["id"]]["provider_id"]]
    raw["refunded_amount"] = {"value": "129.00", "currency": "RUB"}
    assert (await s.refresh(ACCOUNT, second["id"]))["state"] == "refunded"
    assert s.panel.users[2].status == "ACTIVE"
    assert all(
        u.expire_at == datetime(2026, 12, 13, 12, tzinfo=timezone.utc)
        for u in s.panel.users.values()
    )
    assert (await s.store.customer(ACCOUNT))["plan_code"] == "pro"
