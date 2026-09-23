"""Broadcast drafts and admin queries (stream E), plus the /stop opt-out."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from app.services.broadcast_segments import Segment


@dataclass(frozen=True)
class BroadcastInfo:
    id: int
    segment: Segment
    text_html: str
    photo_file_id: Optional[str]
    buttons: list
    disable_notification: bool
    credit_days: int
    total: int
    delivered: int
    failed: int
    blocked: int
    created_at: Optional[datetime]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    @property
    def state(self) -> str:
        if self.finished_at:
            return "done"
        return "running" if self.started_at else "draft"


def _info(bc: Any) -> BroadcastInfo:
    try:
        seg = Segment.from_row(bc.segment, bc.segment_params)
    except ValueError:
        seg = Segment()
    return BroadcastInfo(bc.id, seg, bc.text_html, bc.photo_file_id, list(bc.buttons_json or []),
                         bool(bc.disable_notification), int(bc.credit_days or 0), bc.total, bc.delivered,
                         bc.failed, bc.blocked, bc.created_at, bc.started_at, bc.finished_at)


async def create_draft(*, text_html: str, photo_file_id: Optional[str], buttons: Optional[list],
                       segment: Segment, disable_notification: bool, credit_days: int, created_by: int) -> int:
    from app.db.models import Broadcast
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        bc = Broadcast(text_html=text_html, photo_file_id=photo_file_id, buttons_json=buttons or None,
                       segment=segment.code, segment_params=segment.to_params(),
                       disable_notification=disable_notification, credit_days=int(credit_days) or None,
                       created_by=int(created_by))
        session.add(bc)
        await session.commit()
        await session.refresh(bc)
        return bc.id


async def get_broadcast(broadcast_id: int) -> Optional[BroadcastInfo]:
    from app.db.models import Broadcast
    from app.db.session import SessionLocal

    if not SessionLocal:
        return None
    async with SessionLocal() as session:
        bc = await session.get(Broadcast, int(broadcast_id))
        return _info(bc) if bc else None


async def list_broadcasts(limit: int = 20) -> list[BroadcastInfo]:
    from sqlalchemy import select

    from app.db.models import Broadcast
    from app.db.session import SessionLocal

    if not SessionLocal:
        return []
    async with SessionLocal() as session:
        rows = (await session.execute(select(Broadcast).order_by(Broadcast.id.desc()).limit(limit))).scalars().all()
        return [_info(bc) for bc in rows]


async def delete_draft(broadcast_id: int) -> bool:
    from sqlalchemy import delete

    from app.db.models import Broadcast
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        r = await session.execute(delete(Broadcast).where(Broadcast.id == int(broadcast_id),
                                                          Broadcast.started_at.is_(None)))
        await session.commit()
        return bool(r.rowcount)


async def set_opt_out(user_id: int, value: bool) -> None:
    """/stop and «Отписаться» (True), an explicit /start (False)."""
    from sqlalchemy import update

    from app.db.models import TelegramUser
    from app.db.session import SessionLocal

    if not SessionLocal:
        return
    async with SessionLocal() as session:
        await session.execute(update(TelegramUser).where(TelegramUser.telegram_id == int(user_id))
                              .values(broadcast_opt_out=bool(value)))
        await session.commit()
