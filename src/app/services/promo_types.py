"""DTOs and the repository port of the promo engine (stream E)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol

AUDIENCE_ANY = "any"
KIND_DAYS = "days"


@dataclass(frozen=True)
class PromoCodeSpec:
    code: str
    kind: str = KIND_DAYS
    plan_code: Optional[str] = None
    days: Optional[int] = None
    traffic_gb: Optional[int] = None
    devices: Optional[int] = None
    audience: str = AUDIENCE_ANY
    max_uses: Optional[int] = None
    per_user_limit: int = 1
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PromoCodeRow:
    id: int
    code: str
    kind: str
    plan_code: Optional[str] = None
    days: Optional[int] = None
    traffic_gb: Optional[int] = None
    devices: Optional[int] = None
    audience: str = AUDIENCE_ANY
    max_uses: Optional[int] = None
    uses: int = 0
    per_user_limit: int = 1
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    is_active: bool = True
    meta: dict = field(default_factory=dict)
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class RecordResult:
    status: str  # ok | used | error
    payment_id: Optional[int] = None
    redemption_id: Optional[int] = None


@dataclass(frozen=True)
class ReserveResult:
    status: str  # ok | not_found | exhausted | already_used | error
    redemption_id: Optional[int] = None


class PromoRepo(Protocol):
    """Persistence of the engine. SqlPromoRepo in production, an in-memory
    repo in tests (tests/growth/fakes.py) with the same atomicity."""

    async def ensure_user(self, telegram_id: int) -> None: ...
    async def builtin_used(self, code: str, telegram_id: int) -> bool: ...
    async def trial_used(self, telegram_id: int) -> bool: ...
    async def record_builtin(self, code: str, telegram_id: int, meta: dict, *,
                             trial: Optional[tuple[str, int]] = None) -> RecordResult: ...
    async def rollback_builtin(self, code: str, telegram_id: int) -> None: ...
    async def finish(self, redemption_id: Optional[int], ok: bool, reward: Optional[dict] = None) -> None: ...
    async def has_paid(self, telegram_id: int) -> bool: ...
    async def last_paid_plan(self, telegram_id: int) -> Optional[str]: ...
    async def get_code(self, code: str) -> Optional[PromoCodeRow]: ...
    async def get_code_by_id(self, code_id: int) -> Optional[PromoCodeRow]: ...
    async def reserve(self, code_id: int, telegram_id: int) -> ReserveResult: ...
    async def create_code(self, spec: PromoCodeSpec, created_by: Optional[int]) -> Optional[PromoCodeRow]: ...
    async def list_codes(self, limit: int = 20, *, include_gifts: bool = False) -> list[PromoCodeRow]: ...
    async def set_active(self, code_id: int, active: bool) -> bool: ...
    async def find_gift_by_payment(self, payment_id: int) -> Optional[str]: ...
