"""Panel accounts and the DB rows behind them (stream B).

Two things live here:

1. ``AccountsRepo``: the only DB access of the panel services (telegram_users,
   remna_users, subscriptions). ``SqlAccountsRepo`` is the SQLAlchemy
   implementation; tests use an in-memory one. Rows travel as plain
   dataclasses (``SubRow``, ``TgUserRow``) with aware UTC datetimes; the DB
   keeps naive UTC.

2. ``PanelAccounts``: finds the user's MAIN panel account. It never creates
   one: in 3.0 only ProvisioningService creates panel users (trial, payment,
   promo, admin grant). /start, status, devices and the site only look up.

Lookup order: the id stored in telegram_users.remna_user_id (a 404, a legacy
UUID or an account bound to another Telegram id is treated as stale and the
link is cleared), then the telegramId lookup in the panel (primary = ACTIVE,
latest expireAt). A panel error is raised, never read as "no account".
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

from app.domain.models import PanelUser, SubKind, ensure_utc
from app.logger import logger


def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def numeric_panel_id(value: Any) -> Optional[int]:
    """Numeric 3.x panel id or None (empty, legacy UUID, garbage)."""
    if value in (None, ""):
        return None
    s = str(value).strip()
    if not s.isdigit():
        return None
    n = int(s)
    return n if n > 0 else None


@dataclass
class TgUserRow:
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    remna_user_id: Optional[str] = None


@dataclass
class SubRow:
    """A subscriptions row. ``id`` is None until saved."""

    telegram_user_id: int
    sub_kind: str = SubKind.MAIN.value
    plan_code: str = ""
    id: Optional[int] = None
    plan_name: Optional[str] = None
    active: bool = False
    valid_until: Optional[datetime] = None
    is_lifetime: bool = False
    remna_user_id: Optional[str] = None
    provisioning_state: str = "pending"
    remnawave_synced_at: Optional[datetime] = None
    remnawave_expected_expire_at: Optional[datetime] = None
    last_provisioning_attempt_at: Optional[datetime] = None
    last_provisioning_error: Optional[str] = None
    config_data: dict = field(default_factory=dict)
    autorenew: Optional[bool] = None
    grace_until: Optional[datetime] = None
    grace_state: Optional[str] = None


_SUB_FIELDS = (
    "telegram_user_id", "sub_kind", "plan_code", "plan_name", "active", "valid_until", "is_lifetime",
    "remna_user_id", "provisioning_state", "remnawave_synced_at", "remnawave_expected_expire_at",
    "last_provisioning_attempt_at", "last_provisioning_error", "config_data", "autorenew",
    "grace_until", "grace_state",
)
_DT_FIELDS = frozenset({
    "valid_until", "remnawave_synced_at", "remnawave_expected_expire_at",
    "last_provisioning_attempt_at", "grace_until",
})


class AccountsRepo(Protocol):
    async def get_tg_user(self, telegram_id: int) -> Optional[TgUserRow]: ...

    async def ensure_tg_user(self, telegram_id: int) -> None:
        """INSERT telegram_users ON CONFLICT DO NOTHING (FK invariant)."""

    async def set_panel_link(self, telegram_id: int, panel_id: int, *, username: Optional[str] = None,
                             raw: Optional[dict] = None) -> None:
        """remna_users row (FK) + telegram_users.remna_user_id = panel_id."""

    async def clear_panel_link(self, telegram_id: int, stale_id: str) -> None: ...

    async def get_subscription(self, telegram_id: int, sub_kind: SubKind = SubKind.MAIN) -> Optional[SubRow]:
        """The active row of this kind, else the newest one, else None."""

    async def save_subscription(self, row: SubRow) -> SubRow:
        """INSERT (id None) or UPDATE by id; returns the row with its id."""

    async def list_subscriptions(self, *, sub_kind: Optional[SubKind] = None, active: Optional[bool] = True,
                                 after_id: int = 0, limit: int = 500) -> list[SubRow]:
        """Rows ordered by id (keyset paging with ``after_id``)."""


# ---------------------------------------------------------------------------
# SQLAlchemy implementation
# ---------------------------------------------------------------------------


def _row_from_model(m) -> SubRow:
    data = {f: getattr(m, f) for f in _SUB_FIELDS}
    for f in _DT_FIELDS:
        data[f] = ensure_utc(data[f])
    data["config_data"] = dict(data["config_data"] or {})
    data["active"] = bool(data["active"])
    data["is_lifetime"] = bool(data["is_lifetime"])
    return SubRow(id=m.id, **data)


class SqlAccountsRepo:
    """AccountsRepo over app.db (``session_factory`` defaults to SessionLocal)."""

    def __init__(self, session_factory: Optional[Callable[[], Any]] = None):
        self._factory = session_factory

    def _session(self):
        factory = self._factory
        if factory is None:
            from app.db import session as db_session

            factory = db_session.SessionLocal
        if factory is None:
            raise RuntimeError("database is not configured (DATABASE_URL)")
        return factory()

    async def get_tg_user(self, telegram_id: int) -> Optional[TgUserRow]:
        from sqlalchemy import select

        from app.db.models import TelegramUser

        async with self._session() as s:
            u = (await s.execute(select(TelegramUser).where(TelegramUser.telegram_id == int(telegram_id)))
                 ).scalar_one_or_none()
            if u is None:
                return None
            return TgUserRow(u.telegram_id, u.username, u.first_name, u.last_name, u.remna_user_id)

    async def ensure_tg_user(self, telegram_id: int) -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.models import TelegramUser

        async with self._session() as s:
            await s.execute(
                pg_insert(TelegramUser).values(telegram_id=int(telegram_id))
                .on_conflict_do_nothing(index_elements=["telegram_id"])
            )
            await s.commit()

    async def set_panel_link(self, telegram_id: int, panel_id: int, *, username: Optional[str] = None,
                             raw: Optional[dict] = None) -> None:
        from sqlalchemy import update
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.models import RemnaUser, TelegramUser
        from app.services.remna_service import safe_remna_raw

        rid = str(int(panel_id))
        values: dict = {"remna_id": rid, "username": username}
        safe = safe_remna_raw(raw) if raw else None
        if safe:
            values["raw_data"] = safe
        async with self._session() as s:
            await s.execute(pg_insert(RemnaUser).values(**values).on_conflict_do_nothing(index_elements=["remna_id"]))
            await s.execute(
                update(TelegramUser).where(TelegramUser.telegram_id == int(telegram_id)).values(remna_user_id=rid)
            )
            await s.commit()

    async def clear_panel_link(self, telegram_id: int, stale_id: str) -> None:
        from sqlalchemy import update

        from app.db.models import Subscription, TelegramUser

        async with self._session() as s:
            await s.execute(
                update(TelegramUser)
                .where(TelegramUser.telegram_id == int(telegram_id), TelegramUser.remna_user_id == str(stale_id))
                .values(remna_user_id=None)
            )
            await s.execute(
                update(Subscription)
                .where(Subscription.telegram_user_id == int(telegram_id),
                       Subscription.sub_kind == SubKind.MAIN.value,
                       Subscription.remna_user_id == str(stale_id))
                .values(remna_user_id=None)
            )
            await s.commit()

    async def get_subscription(self, telegram_id: int, sub_kind: SubKind = SubKind.MAIN) -> Optional[SubRow]:
        from sqlalchemy import select

        from app.db.models import Subscription

        async with self._session() as s:
            m = (await s.execute(
                select(Subscription)
                .where(Subscription.telegram_user_id == int(telegram_id),
                       Subscription.sub_kind == SubKind(sub_kind).value)
                .order_by(Subscription.active.desc(), Subscription.id.desc())
                .limit(1)
            )).scalar_one_or_none()
            return _row_from_model(m) if m else None

    async def save_subscription(self, row: SubRow) -> SubRow:
        from sqlalchemy import select

        from app.db.models import RemnaUser, Subscription

        values = {f: getattr(row, f) for f in _SUB_FIELDS}
        for f in _DT_FIELDS:
            values[f] = to_naive_utc(values[f])
        values["config_data"] = dict(values["config_data"] or {})
        async with self._session() as s:
            if values.get("remna_user_id"):
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                await s.execute(
                    pg_insert(RemnaUser).values(remna_id=str(values["remna_user_id"]))
                    .on_conflict_do_nothing(index_elements=["remna_id"])
                )
            if row.id is None:
                m = Subscription(**values)
                s.add(m)
            else:
                m = (await s.execute(
                    select(Subscription).where(Subscription.id == row.id, Subscription.sub_kind == row.sub_kind)
                )).scalar_one()
                for k, v in values.items():
                    setattr(m, k, v)
            await s.commit()
            return replace(row, id=m.id)

    async def list_subscriptions(self, *, sub_kind: Optional[SubKind] = None, active: Optional[bool] = True,
                                 after_id: int = 0, limit: int = 500) -> list[SubRow]:
        from sqlalchemy import select

        from app.db.models import Subscription

        q = select(Subscription).where(Subscription.id > int(after_id))
        if sub_kind is not None:
            q = q.where(Subscription.sub_kind == SubKind(sub_kind).value)
        if active is not None:
            q = q.where(Subscription.active.is_(bool(active)))
        q = q.order_by(Subscription.id.asc()).limit(int(limit))
        async with self._session() as s:
            return [_row_from_model(m) for m in (await s.execute(q)).scalars().all()]


async def iter_all_subscriptions(repo: AccountsRepo, *, sub_kind: Optional[SubKind] = None,
                                 active: Optional[bool] = True, page: int = 500):
    """Every matching row, paged by id (no fixed-window blind spot, 06 M2)."""
    after = 0
    while True:
        rows = await repo.list_subscriptions(sub_kind=sub_kind, active=active, after_id=after, limit=page)
        if not rows:
            return
        for r in rows:
            yield r
        after = int(rows[-1].id or after)
        if len(rows) < page:
            return


# ---------------------------------------------------------------------------
# Main panel account lookup (no creation)
# ---------------------------------------------------------------------------


class PanelAccounts:
    def __init__(self, remna, repo: AccountsRepo):
        self.remna = remna
        self.repo = repo

    async def find_main(self, telegram_id: int) -> Optional[PanelUser]:
        """The user's main panel account or None. Panel errors are raised."""
        tg = int(telegram_id)
        stored_raw = None
        try:
            row = await self.repo.get_tg_user(tg)
            stored_raw = row.remna_user_id if row else None
        except Exception as e:  # noqa: BLE001 - DB down: the panel lookup still works
            logger.warning(f"accounts: telegram_users read failed tg={tg} ({type(e).__name__})")
        stored = numeric_panel_id(stored_raw)
        if stored is not None:
            user = await self.remna.get_user(stored)
            if user is not None and user.telegram_id in (None, tg):
                return user
            if user is None:
                logger.warning(f"accounts: stored panel id {stored} for tg={tg} is gone, clearing the link")
            else:
                logger.warning(
                    f"accounts: stored panel id {stored} for tg={tg} is bound to another Telegram id, ignoring it"
                )
            await self._clear(tg, str(stored_raw))
        elif stored_raw:
            logger.warning(f"accounts: legacy non-numeric panel id for tg={tg}, clearing the link")
            await self._clear(tg, str(stored_raw))

        users = await self.remna.find_users_by_telegram_id(tg)
        if not users:
            return None
        primary = users[0]
        if len(users) > 1:
            logger.warning(f"accounts: {len(users)} panel users with telegramId={tg}, using id={primary.id}")
        try:
            await self.repo.set_panel_link(tg, primary.id, username=primary.username, raw=dict(primary.raw))
        except Exception as e:  # noqa: BLE001 - the link is a cache of the lookup
            logger.warning(f"accounts: could not store panel link tg={tg} ({type(e).__name__})")
        return primary

    async def _clear(self, tg: int, stale: str) -> None:
        try:
            await self.repo.clear_panel_link(tg, stale)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"accounts: could not clear stale panel link tg={tg} ({type(e).__name__})")
