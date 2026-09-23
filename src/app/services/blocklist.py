"""Стоп-лист: кому не продаем.

Две таблицы:
  blocked_users  — telegram_id, проверяется ДО создания платежа (деньги не берем);
  blocked_cards  — отпечаток карты (first6-last4-MM/YY), проверяется в вебхуке
                   уже после оплаты, поэтому только уведомляет админа.

Заполняется вручную через SQL, без UI:
  INSERT INTO blocked_users (telegram_id, reason) VALUES (123456, 'конкурент');
  INSERT INTO blocked_cards (fingerprint, reason) VALUES ('220220-7882-09/2028', 'конкурент');
"""
from typing import Optional, Dict, Any

from sqlalchemy import text

from app.db.session import SessionLocal
from app.logger import logger


def card_fingerprint(card: Optional[Dict[str, Any]]) -> Optional[str]:
    """Отпечаток карты из объекта payment_method.card в вебхуке YooKassa."""
    if not card:
        return None
    first6 = card.get("first6")
    last4 = card.get("last4")
    month = card.get("expiry_month")
    year = card.get("expiry_year")
    if not (first6 and last4 and month and year):
        return None
    return f"{first6}-{last4}-{month}/{year}"


async def get_user_block_reason(telegram_id: int) -> Optional[str]:
    """Причина блокировки пользователя, или None. Ошибки БД не блокируют продажу."""
    if not SessionLocal or not telegram_id:
        return None
    try:
        async with SessionLocal() as session:
            row = await session.execute(
                text("SELECT reason FROM blocked_users WHERE telegram_id = :tid"),
                {"tid": int(telegram_id)},
            )
            found = row.scalar_one_or_none()
            return found if found is not None else None
    except Exception as e:
        logger.warning(f"blocklist: user check failed tg_id={telegram_id} err={e}")
        return None


async def get_card_block_reason(fingerprint: Optional[str]) -> Optional[str]:
    """Причина блокировки карты, или None."""
    if not SessionLocal or not fingerprint:
        return None
    try:
        async with SessionLocal() as session:
            row = await session.execute(
                text("SELECT reason FROM blocked_cards WHERE fingerprint = :fp"),
                {"fp": fingerprint},
            )
            found = row.scalar_one_or_none()
            return found if found is not None else None
    except Exception as e:
        logger.warning(f"blocklist: card check failed fp={fingerprint} err={e}")
        return None


# =============================================================================
# 3.0 (stream E): admin control of the stop-list and of the bot blocklist.
# =============================================================================

import re as _re
from dataclasses import dataclass as _dataclass
from datetime import datetime as _datetime

CARD_FP_RE = _re.compile(r"^\d{6}-\d{4}-\d{1,2}/\d{2,4}$")


@_dataclass(frozen=True)
class StopEntry:
    key: str  # telegram id or card fingerprint
    reason: Optional[str]
    blocked_at: Optional[_datetime]


class BlocklistAdmin:
    """Two lists, both managed from the admin bot:

    - stop-list (tables blocked_users / blocked_cards): nobody on it can buy;
      checked before a payment is created and in the webhook (2.x);
    - bot blocklist (Redis set + BLOCKED_TELEGRAM_IDS): the bot ignores the
      user completely (app.middlewares.blocklist).
    """

    @staticmethod
    def _session():
        if not SessionLocal:
            raise RuntimeError("database is not configured")
        return SessionLocal()

    async def stop_list(self) -> tuple[list[StopEntry], list[StopEntry]]:
        from sqlalchemy import select

        from app.db.models import BlockedCard, BlockedUser

        async with self._session() as s:
            users = (await s.execute(select(BlockedUser).order_by(BlockedUser.blocked_at.desc()))).scalars().all()
            cards = (await s.execute(select(BlockedCard).order_by(BlockedCard.blocked_at.desc()))).scalars().all()
        return ([StopEntry(str(u.telegram_id), u.reason, u.blocked_at) for u in users],
                [StopEntry(c.fingerprint, c.reason, c.blocked_at) for c in cards])

    async def stop_user(self, telegram_id: int, reason: str = "") -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.models import BlockedUser

        async with self._session() as s:
            stmt = pg_insert(BlockedUser).values(telegram_id=int(telegram_id), reason=reason or None)
            await s.execute(stmt.on_conflict_do_update(index_elements=["telegram_id"], set_={"reason": reason or None}))
            await s.commit()

    async def unstop_user(self, telegram_id: int) -> bool:
        from sqlalchemy import delete

        from app.db.models import BlockedUser

        async with self._session() as s:
            r = await s.execute(delete(BlockedUser).where(BlockedUser.telegram_id == int(telegram_id)))
            await s.commit()
            return bool(r.rowcount)

    async def stop_card(self, fingerprint: str, reason: str = "") -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.models import BlockedCard

        if not CARD_FP_RE.match(fingerprint or ""):
            raise ValueError("fingerprint must look like 220220-7882-09/2028")
        async with self._session() as s:
            stmt = pg_insert(BlockedCard).values(fingerprint=fingerprint, reason=reason or None)
            await s.execute(stmt.on_conflict_do_update(index_elements=["fingerprint"], set_={"reason": reason or None}))
            await s.commit()

    async def unstop_card(self, fingerprint: str) -> bool:
        from sqlalchemy import delete

        from app.db.models import BlockedCard

        async with self._session() as s:
            r = await s.execute(delete(BlockedCard).where(BlockedCard.fingerprint == fingerprint))
            await s.commit()
            return bool(r.rowcount)

    @staticmethod
    async def bot_block(telegram_id: int) -> None:
        from app.middlewares.blocklist import block_user

        await block_user(int(telegram_id))

    @staticmethod
    async def bot_unblock(telegram_id: int) -> None:
        from app.middlewares.blocklist import unblock_user

        await unblock_user(int(telegram_id))

    @staticmethod
    def bot_blocked(telegram_id: int) -> bool:
        from app.middlewares.blocklist import is_blocked

        return is_blocked(int(telegram_id))
