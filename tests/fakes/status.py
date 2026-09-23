"""FakeStatusService: implements app.services.ports.StatusService in memory.

Default: no panel user, inactive, not stale. Tests set ``fake.states[uid]``
to a custom SubscriptionState before pressing a button.
"""
from __future__ import annotations

from typing import Dict

from app.domain.models import SubscriptionState


class FakeStatusService:
    def __init__(self) -> None:
        self.states: Dict[int, SubscriptionState] = {}
        self.invalidated: list[int] = []
        self.calls: list[tuple[int, bool]] = []

    async def get_state(self, telegram_id: int, *, force: bool = False) -> SubscriptionState:
        self.calls.append((int(telegram_id), force))
        return self.states.get(int(telegram_id)) or SubscriptionState(telegram_id=int(telegram_id))

    async def invalidate(self, telegram_id: int) -> None:
        self.invalidated.append(int(telegram_id))
