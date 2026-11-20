"""Durable site commerce ledger. Only this module accesses its SQL tables."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.db.models import SiteCustomer, SiteOrder
from app.db import session as db


def now():
    return datetime.now(timezone.utc)


def customer_dict(row):
    return {
        "account_id": row.account_id,
        "telegram_id": row.telegram_id,
        "backend": row.backend,
        **dict(row.payload),
    }


def order_dict(row):
    return {
        "id": row.id,
        "account_id": row.account_id,
        "state": row.state,
        "provider_id": row.provider_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        **dict(row.payload),
    }


class SiteBusy(Exception):
    pass


class SqlSiteStore:
    @asynccontextmanager
    async def lock(self, account_id):
        # Session lock survives the commit-before-provision boundary; the same
        # physical connection releases it in finally before returning to the pool.
        if db.engine is None:
            raise RuntimeError("database unavailable")
        async with db.engine.connect() as conn:
            key = "site-commerce:" + str(account_id)
            acquired = (
                await conn.execute(
                    text("select pg_try_advisory_lock(hashtextextended(:key, 0))"),
                    {"key": key},
                )
            ).scalar()
            await conn.commit()
            if not acquired:
                raise SiteBusy()
            try:
                yield
            finally:
                try:
                    await conn.execute(
                        text("select pg_advisory_unlock(hashtextextended(:key, 0))"),
                        {"key": key},
                    )
                    await conn.commit()
                except BaseException:
                    await conn.invalidate()
                    raise

    async def customer(self, account_id):
        async with db.SessionLocal() as session:
            row = await session.get(SiteCustomer, str(account_id))
            return customer_dict(row) if row else None

    async def save_customer(self, data):
        payload = {
            k: v
            for k, v in data.items()
            if k not in {"account_id", "telegram_id", "backend"}
        }
        async with db.SessionLocal() as session:
            row = await session.get(SiteCustomer, data["account_id"])
            if row is None:
                row = SiteCustomer(
                    account_id=data["account_id"],
                    backend=data["backend"],
                    telegram_id=data.get("telegram_id"),
                    payload=payload,
                )
                session.add(row)
            else:
                row.telegram_id, row.payload = data.get("telegram_id"), payload
            await session.commit()

    async def order(self, order_id):
        async with db.SessionLocal() as session:
            row = await session.get(SiteOrder, str(order_id))
            return order_dict(row) if row else None

    async def external(self, provider_id):
        async with db.SessionLocal() as session:
            row = (
                await session.execute(
                    select(SiteOrder).where(SiteOrder.provider_id == provider_id)
                )
            ).scalar_one_or_none()
            return order_dict(row) if row else None

    async def save_order(self, data):
        columns = {
            "id",
            "account_id",
            "state",
            "provider_id",
            "created_at",
            "updated_at",
        }
        payload = {k: v for k, v in data.items() if k not in columns}
        async with db.SessionLocal() as session:
            row = await session.get(SiteOrder, data["id"])
            if row is None:
                row = SiteOrder(
                    id=data["id"],
                    account_id=data["account_id"],
                    state=data["state"],
                    provider_id=data.get("provider_id"),
                    payload=payload,
                    created_at=data.get("created_at", now()),
                    updated_at=now(),
                )
                session.add(row)
            else:
                row.state, row.provider_id, row.payload = (
                    data["state"],
                    data.get("provider_id"),
                    payload,
                )
                row.updated_at = now()
            await session.commit()
        data["updated_at"] = now()

    async def orders(self, account_id):
        async with db.SessionLocal() as session:
            rows = (
                await session.execute(
                    select(SiteOrder)
                    .where(SiteOrder.account_id == str(account_id))
                    .order_by(SiteOrder.created_at.desc())
                    .limit(100)
                )
            ).scalars()
            return [order_dict(row) for row in rows]

    async def recovery(self):
        from datetime import timedelta

        async with db.SessionLocal() as session:
            rows = (
                await session.execute(
                    select(SiteOrder)
                    .where(
                        SiteOrder.state.in_(
                            [
                                "creating",
                                "pending",
                                "paid",
                                "provisioning",
                                "refunding",
                                "fulfilled",
                            ]
                        ),
                        SiteOrder.created_at > now() - timedelta(days=31),
                    )
                    .order_by(SiteOrder.updated_at)
                    .limit(50)
                )
            ).scalars()
            return [order_dict(row) for row in rows]
