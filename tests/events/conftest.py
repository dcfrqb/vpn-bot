"""Fixtures of stream C: in-memory events repo, checkout, redis, container."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from app.domain.models import Quote
from app.services.events_repo import (
    EXPIRE_ALREADY,
    EXPIRE_MARKED,
    EXPIRE_NO_ROW,
    EXPIRE_PAID_LATER,
    GRACE_ACTIVE,
    GraceRow,
    ReminderInfo,
)
from tests.fakes.bot import make_bot
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.redis import FakeRedis
from tests.fakes.remnawave import FakeRemna, FakeRemnaGateway

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)  # 12:00 MSK


class FakeEventsRepo:
    """EventsRepo in memory. ``subs[tg]`` is the main row as a dict."""

    def __init__(self):
        self.info: dict[int, ReminderInfo] = {}
        self.subs: dict[int, dict] = {}
        self.obhod: dict[int, dict] = {}
        self.calls: list[tuple] = []

    def add(self, tg: int, *, panel_id: int = 0, valid_until: Optional[datetime] = None, active: bool = True,
            lifetime: bool = False, **info):
        self.subs[tg] = {"active": active, "valid_until": valid_until, "is_lifetime": lifetime,
                         "grace_until": None, "grace_state": None, "remna_user_id": str(panel_id) if panel_id else None}
        self.info[tg] = ReminderInfo(telegram_id=tg, is_lifetime=lifetime, **info)

    async def reminder_info(self, telegram_id: int) -> ReminderInfo:
        base = self.info.get(telegram_id, ReminderInfo(telegram_id=telegram_id))
        sub = self.subs.get(telegram_id)
        return replace(base, grace_state=sub["grace_state"]) if sub else base

    async def mark_main_expired(self, telegram_id: int, now: datetime) -> str:
        self.calls.append(("mark_expired", telegram_id))
        sub = self.subs.get(telegram_id)
        if sub is None:
            return EXPIRE_NO_ROW
        if sub["is_lifetime"] or (sub["valid_until"] and sub["valid_until"] > now + timedelta(minutes=5)):
            return EXPIRE_PAID_LATER
        if not sub["active"]:
            return EXPIRE_ALREADY
        sub["active"] = False
        return EXPIRE_MARKED

    async def obhod_panel_id(self, telegram_id: int):
        row = self.obhod.get(telegram_id)
        return row["panel_id"] if row and row["active"] else None

    async def deactivate_obhod_row(self, telegram_id: int) -> bool:
        row = self.obhod.get(telegram_id)
        if not row or not row["active"]:
            return False
        row["active"] = False
        return True

    async def obhod_owner(self, panel_id: int, uuid: str = ""):
        for tg, row in self.obhod.items():
            if row["panel_id"] == str(panel_id):
                return tg
        return None

    async def get_grace(self, telegram_id: int):
        sub = self.subs.get(telegram_id)
        if not sub:
            return None
        return GraceRow(telegram_id, sub["grace_until"], sub["grace_state"], sub["remna_user_id"])

    async def set_grace(self, telegram_id: int, *, until, state) -> bool:
        self.calls.append(("set_grace", telegram_id, until, state))
        sub = self.subs.get(telegram_id)
        if not sub:
            return False
        sub["grace_until"], sub["grace_state"] = until, state
        return True

    async def due_graces(self, now: datetime):
        return [GraceRow(tg, s["grace_until"], s["grace_state"], s["remna_user_id"])
                for tg, s in self.subs.items()
                if s["grace_state"] == GRACE_ACTIVE and s["grace_until"] and s["grace_until"] <= now]


class FakeCheckout:
    """CheckoutService.quote only: sells what is in ``sellable``."""

    def __init__(self, sellable=(("pro", 1), ("pro", 3), ("standard", 1), ("lite", 12))):
        self.sellable = set(sellable)

    async def quote(self, telegram_id, plan_code, months):
        if (plan_code, months) not in self.sellable:
            return None
        return Quote(plan_code=plan_code, months=months, amount_rub=1)


class ReadyProvisioning:
    """Stand-in for stream B's ProvisioningService (anything but the 2.x shim)."""

    async def grant(self, *a, **k):
        raise AssertionError("grace must never call grant")

    async def revoke(self, *a, **k):
        raise AssertionError("grace must never call revoke")


class RecordingStatus:
    def __init__(self):
        self.invalidated: list[int] = []

    async def get_state(self, telegram_id, *, force=False):
        raise AssertionError("not used")

    async def invalidate(self, telegram_id):
        self.invalidated.append(int(telegram_id))


class Settings:
    """Plain settings object for services (only what stream C reads)."""

    def __init__(self, **kw):
        self.GRACE_ENABLED = False
        self.GRACE_SQUAD = "grace"
        self.GRACE_DAYS = 3
        self.GRACE_DAILY_GB = 5
        self.MAINTENANCE_AUTO_ENABLED = False
        self.SUPPORT_HANDLE = "dcfrq"
        self.ADMIN_SUPPORT_USERNAME = None
        self.CONNECT_ARTICLE_URL = "https://telegra.ph/crs"
        self.PANEL_WEBHOOK_SECRET = None
        self.ADMINS = [1]
        self.__dict__.update(kw)


@pytest.fixture
def redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr("app.services.cache.get_redis_client", lambda: r)
    return r


@pytest.fixture
def panel():
    squads = dict(FakeRemna().squads)
    squads["grace"] = "sq-grace"
    return FakeRemna(squads=squads)


@pytest.fixture
def env(panel, redis):
    """Container of fakes + repo + settings for stream C tests."""
    from app.container import build_container

    bot, session = make_bot()
    settings = Settings()
    container = build_container(
        bot,
        settings=settings,
        remna=FakeRemnaGateway(panel),
        notifier=RecordingNotifier(),
        checkout=FakeCheckout(),
        status=RecordingStatus(),
        provisioning=ReadyProvisioning(),
    )

    class Env:
        pass

    e = Env()
    e.bot, e.session, e.settings, e.c, e.panel, e.redis = bot, session, settings, container, panel, redis
    e.repo = FakeEventsRepo()
    e.notifier = container.notifier
    return e
