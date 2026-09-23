"""Stream B fixtures: in-memory panel (FakeRemna + the real HttpRemnaGateway),
in-memory AccountsRepo, FakeRedis, RecordingNotifier, a fixed clock."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import patch

import pytest

from app.domain.models import SubKind
from app.services.accounts import SubRow, TgUserRow
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.redis import FakeRedis
from tests.fakes.remnawave import FakeRemna, FakeRemnaGateway

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class InMemoryAccountsRepo:
    """AccountsRepo in memory, same semantics as SqlAccountsRepo."""

    def __init__(self):
        self.users: dict[int, TgUserRow] = {}
        self.subs: dict[int, SubRow] = {}
        self.remna_rows: set[str] = set()
        self._next = 1
        self.fail = False
        self.saves = 0

    def _check(self):
        if self.fail:
            raise RuntimeError("db down")

    async def get_tg_user(self, telegram_id):
        self._check()
        u = self.users.get(int(telegram_id))
        return replace(u) if u else None

    async def ensure_tg_user(self, telegram_id):
        self._check()
        self.users.setdefault(int(telegram_id), TgUserRow(int(telegram_id)))

    async def set_panel_link(self, telegram_id, panel_id, *, username=None, raw=None):
        self._check()
        self.remna_rows.add(str(panel_id))
        u = self.users.get(int(telegram_id))
        if u:
            u.remna_user_id = str(panel_id)

    async def clear_panel_link(self, telegram_id, stale_id):
        self._check()
        u = self.users.get(int(telegram_id))
        if u and u.remna_user_id == str(stale_id):
            u.remna_user_id = None

    async def get_subscription(self, telegram_id, sub_kind=SubKind.MAIN):
        self._check()
        rows = [r for r in self.subs.values()
                if r.telegram_user_id == int(telegram_id) and r.sub_kind == SubKind(sub_kind).value]
        if not rows:
            return None
        rows.sort(key=lambda r: (r.active, r.id), reverse=True)
        return replace(rows[0], config_data=dict(rows[0].config_data))

    async def save_subscription(self, row):
        self._check()
        self.saves += 1
        if row.id is None:
            row = replace(row, id=self._next)
            self._next += 1
        if row.active:
            for other in self.subs.values():
                if other.id != row.id and other.active and other.telegram_user_id == row.telegram_user_id \
                        and other.sub_kind == row.sub_kind:
                    raise AssertionError("two active rows of one kind (uq_active_subscription_per_user_kind)")
        self.subs[row.id] = replace(row, config_data=dict(row.config_data))
        return replace(row)

    async def list_subscriptions(self, *, sub_kind=None, active=True, after_id=0, limit=500):
        self._check()
        rows = sorted(self.subs.values(), key=lambda r: r.id)
        out = [replace(r) for r in rows
               if r.id > after_id
               and (sub_kind is None or r.sub_kind == SubKind(sub_kind).value)
               and (active is None or r.active == active)]
        return out[:limit]

    # helpers
    def add_row(self, tg: int, *, kind: str = "main", plan: str = "pro", active: bool = True,
                until: Optional[datetime] = None, panel_id: Optional[int] = None, state: str = "synced",
                **kw) -> SubRow:
        self.users.setdefault(tg, TgUserRow(tg))
        kw.setdefault("remna_user_id", str(panel_id) if panel_id else None)
        row = SubRow(telegram_user_id=tg, sub_kind=kind, plan_code=plan, id=self._next, active=active,
                     valid_until=until, provisioning_state=state, **kw)
        self._next += 1
        self.subs[row.id] = row
        return row


class Clock:
    def __init__(self, now: datetime = NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def redis():
    r = FakeRedis()
    with patch("app.services.cache.get_redis_client", return_value=r):
        yield r


@pytest.fixture
def fake() -> FakeRemna:
    return FakeRemna()


@pytest.fixture
def gw(fake):
    return FakeRemnaGateway(fake)


@pytest.fixture
def repo() -> InMemoryAccountsRepo:
    return InMemoryAccountsRepo()


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def clock() -> Clock:
    return Clock()
