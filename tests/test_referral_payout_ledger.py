# tests/test_referral_payout_ledger.py
"""Тесты выноса referral_payout в отдельный леджер referral_payouts.

Регрессия: раньше выплата писалась в payments как 0₽ provider='referral_payout'
status='succeeded' и платёжный воркер принимал её за покупку. Теперь — отдельная
таблица, которую воркеры не видят.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.referral_tracker import (
    record_payout,
    compute_sun718_paid_out,
    notify_payout,
)
from app.db.models import ReferralPayout, Payment as PaymentModel


@pytest.mark.asyncio
async def test_record_payout_writes_ledger_not_payment():
    """record_payout создаёт ReferralPayout, а НЕ Payment."""
    session = AsyncMock()
    session.add = MagicMock()

    rec = await record_payout(session, months=2, note="продлил руками", admin_id=5657723056)

    assert isinstance(rec, ReferralPayout)
    assert not isinstance(rec, PaymentModel)
    added = session.add.call_args[0][0]
    assert isinstance(added, ReferralPayout)
    assert added.payout_months == 2
    assert added.admin_id == 5657723056
    assert added.promo_code == "sun718"
    assert added.note == "продлил руками"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_payout_empty_note_is_null():
    """Пустой комментарий сохраняется как None, не как ''."""
    session = AsyncMock()
    session.add = MagicMock()
    rec = await record_payout(session, months=1, note="", admin_id=1)
    assert rec.note is None


@pytest.mark.asyncio
async def test_compute_paid_out_reads_ledger_sum():
    """compute_sun718_paid_out читает SUM(payout_months) из леджера."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = 7
    session.execute = AsyncMock(return_value=result)

    total = await compute_sun718_paid_out(session)
    assert total == 7


@pytest.mark.asyncio
async def test_compute_paid_out_zero_when_empty():
    """Пустой леджер → 0 (coalesce)."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = 0
    session.execute = AsyncMock(return_value=result)

    assert await compute_sun718_paid_out(session) == 0


@pytest.mark.asyncio
async def test_notify_payout_reads_object_fields():
    """notify_payout берёт months/note из объекта ReferralPayout, не из metadata."""
    payout = ReferralPayout(admin_id=111, promo_code="sun718", payout_months=3, note="hi")
    bot = AsyncMock()

    with patch("app.services.referral_tracker.compute_sun718_paid_out", AsyncMock(return_value=3)), \
         patch("app.services.referral_tracker.compute_sun718_earned_months", AsyncMock(return_value=20)), \
         patch("app.services.referral_tracker._admin_ids", return_value=[111]), \
         patch("app.services.referral_tracker._owner_id", return_value=None):
        await notify_payout(bot, AsyncMock(), payout)

    assert bot.send_message.await_count == 1
    sent = bot.send_message.await_args.kwargs.get("text") or bot.send_message.await_args.args
    text = sent if isinstance(sent, str) else " ".join(str(a) for a in sent)
    assert "3 мес" in text
    assert "hi" in text
