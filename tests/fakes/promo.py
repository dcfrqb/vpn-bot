"""FakePromoService: implements app.services.ports.PromoService in memory.

Conservative defaults (trial_available=False, redeem/start_trial not eligible)
so tests are explicit about what they exercise; flip the attributes to test
the "trial available" / "applied" paths.
"""
from __future__ import annotations

from app.domain.models import PromoOutcome, PromoReward


class FakePromoService:
    def __init__(self) -> None:
        self.available = False
        self.trial_outcome = PromoOutcome.NOT_ELIGIBLE
        self.redeem_outcome = PromoOutcome.NOT_FOUND
        self.trial_days = 5
        self.calls: list[tuple[str, int]] = []
        self.raise_not_implemented = False

    async def trial_available(self, telegram_id: int) -> bool:
        self.calls.append(("trial_available", int(telegram_id)))
        if self.raise_not_implemented:
            raise NotImplementedError("PromoService.trial_available: fake set to raise")
        return self.available

    async def start_trial(self, telegram_id: int) -> PromoReward:
        self.calls.append(("start_trial", int(telegram_id)))
        if self.raise_not_implemented:
            raise NotImplementedError("PromoService.start_trial: fake set to raise")
        outcome = PromoOutcome.APPLIED if self.available else self.trial_outcome
        return PromoReward(code="trial", outcome=outcome, plan_code="standard", days=self.trial_days)

    async def redeem(self, telegram_id: int, code: str, *, source: str = "command") -> PromoReward:
        self.calls.append(("redeem", int(telegram_id)))
        if self.raise_not_implemented:
            raise NotImplementedError("PromoService.redeem: fake set to raise")
        return PromoReward(code=code, outcome=self.redeem_outcome)
