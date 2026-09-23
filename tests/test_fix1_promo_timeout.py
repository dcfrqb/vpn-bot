"""Фикс-раунд 1, ревью m3/m-3: таймаут ответа панели после примененного PATCH
больше не откатывает промо-запись (иначе /trial брался повторно поверх уже
продленного срока). provision_tariff перечитывает юзера и признает выдачу."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tests.fakes.remnawave import FakeRemna

TG_ID = 900000071


@pytest.fixture(autouse=True)
def _no_recheck_delay(monkeypatch):
    from app.services import remna_service

    monkeypatch.setattr(remna_service, "GRANT_RECHECK_DELAY_SECONDS", 0)


class _SlowReplyRemna(FakeRemna):
    """PATCH применяется, но ответ «теряется» (таймаут)."""

    def __init__(self, apply_before_error: bool):
        super().__init__()
        self.apply_before_error = apply_before_error

    async def update_user(self, user_id, **kwargs):
        if self.apply_before_error:
            await super().update_user(user_id, **kwargs)
        raise asyncio.TimeoutError()


async def _provision(fake):
    from app.services import remna_service

    fake.add_user(71, "tg_test_user", telegram_id=TG_ID, squads=[], expire="2000-01-01T00:00:00Z")
    with patch.object(remna_service, "RemnaClient", return_value=fake), \
         patch.object(remna_service, "ensure_user_in_remnawave", AsyncMock(return_value="71")), \
         patch("app.db.session.SessionLocal", None):
        return await remna_service.provision_tariff(TG_ID, "trial_standard_5d", req_id="t")


@pytest.mark.asyncio
async def test_patch_landed_but_reply_timed_out_counts_as_granted():
    fake = _SlowReplyRemna(apply_before_error=True)
    assert await _provision(fake) is True
    assert fake.squad_names(71) == ["standard"]


@pytest.mark.asyncio
async def test_patch_not_applied_is_still_a_failure():
    fake = _SlowReplyRemna(apply_before_error=False)
    assert await _provision(fake) is False
