"""Broadcast segments and credit_days idempotency (no database)."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.services import broadcast as svc
from app.services.grants import add_days
from tests.growth.fakes import FakeProvisioning, FakeStatus, MemoryLedger


def _sql(expr) -> str:
    return str(expr.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _credits(prov, status, ledger):
    return svc.BroadcastCredits(provisioning=prov, status=status, ledger=ledger)


@pytest.fixture
def parts():
    status = FakeStatus()
    return FakeProvisioning(status), status, MemoryLedger()


async def test_credit_replay_never_grants_twice(parts):
    prov, status, ledger = parts
    status.set(11, active=True, plan_code="standard", expires_at=datetime.now(timezone.utc) + timedelta(days=2))
    credit = _credits(prov, status, ledger)
    assert await credit(7, 11, 5) == "applied"
    before = status.states[11].expires_at
    # the same broadcast resumed after a restart / the final sweep
    assert await credit(7, 11, 5) == "dup"
    assert await credit(7, 11, 5) == "dup"
    assert len(prov.calls) == 1 and prov.effective == 1
    assert status.states[11].expires_at == before
    assert await ledger.count(svc.credit_code(7), "applied") == 1


async def test_crash_between_ledger_and_grant_is_absorbed(parts):
    prov, status, ledger = parts
    status.set(12, active=True, plan_code="pro", expires_at=datetime.now(timezone.utc) + timedelta(days=2))
    credit = _credits(prov, status, ledger)
    assert await credit(8, 12, 3) == "applied"
    # simulate: the grant went through but the process died before closing the ledger row
    ledger.rows[(svc.credit_code(8), 12)]["status"] = "pending"
    assert await credit(8, 12, 3) == "applied"
    assert len(prov.calls) == 2 and prov.effective == 1  # same trace_id bc:8:12
    assert {c[2] for c in prov.calls} == {"bc:8:12"}


async def test_parallel_credit_for_same_user_once(parts):
    prov, status, ledger = parts
    status.set(13, active=True, plan_code="lite", expires_at=datetime.now(timezone.utc) + timedelta(days=2))
    prov.delay = 0.005
    credit = _credits(prov, status, ledger)
    await asyncio.gather(*(credit(9, 13, 5) for _ in range(5)))
    assert prov.effective == 1


async def test_credit_skips_users_without_subscription_and_lifetime(parts):
    prov, status, ledger = parts
    credit = _credits(prov, status, ledger)
    assert await credit(10, 14, 5) == "skipped"
    status.set(15, active=True, plan_code="pro", is_lifetime=True)
    assert await credit(10, 15, 5) == "skipped"
    assert not prov.calls
    assert await credit(10, 14, 5) == "dup"  # skipped is final for this broadcast


async def test_credit_failure_is_retried_later(parts):
    prov, status, ledger = parts
    status.set(16, active=True, plan_code="pro", expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    prov.fail = True
    credit = _credits(prov, status, ledger)
    assert await credit(11, 16, 5) == "failed"
    prov.fail = False
    assert await credit(11, 16, 5) == "applied"
    assert prov.effective == 1


async def test_add_days_uses_native_provisioning_add_days_when_present():
    calls = []

    class P:
        async def add_days(self, tg, days, *, trace_id, source):
            calls.append((tg, days, trace_id))
            return "state"

    assert await add_days(P(), FakeStatus(), 5, 3, trace_id="t") == "state"
    assert calls == [(5, 3, "t")]


# ----------------------------------------------------------------- segments


def test_segment_params_roundtrip_and_validation():
    seg = svc.Segment(kind="expired", sub_kind="main", days=14)
    assert svc.Segment.from_row("expired", seg.to_params()) == seg
    ids = svc.Segment(kind="ids", ids=(1, 2, 3))
    assert svc.Segment.from_row("ids", ids.to_params()) == ids
    assert svc.Segment.from_row("active", None) == svc.Segment(kind="active")
    for bad in (dict(kind="vip"), dict(kind="ids"), dict(kind="all", days=0), dict(kind="all", sub_kind="x"),
                dict(kind="ids", ids=tuple(range(1001)))):
        with pytest.raises(ValueError):
            svc.Segment(**bad)
    assert all(len(k) <= 16 for k in svc.SEGMENTS)  # broadcasts.segment is varchar(16)


@pytest.mark.parametrize("kind", ["active", "expired", "trial_nc"])
def test_subscription_segments_filter_sub_kind(kind):
    sql = _sql(svc.segment_filter(svc.Segment(kind=kind, days=7), now=datetime(2026, 9, 23)))
    if kind != "trial_nc":
        assert "subscriptions.sub_kind = 'main'" in sql
    assert "broadcast_opt_out IS false" in sql


def test_obhod_sub_kind_and_windows():
    sql = _sql(svc.segment_filter(svc.Segment(kind="active", sub_kind="obhod", days=3), now=datetime(2026, 9, 23)))
    assert "sub_kind = 'obhod'" in sql and "2026-09-26" in sql
    sql = _sql(svc.segment_filter(svc.Segment(kind="expired", days=30), now=datetime(2026, 9, 23)))
    assert "2026-08-24" in sql


def test_never_and_trial_nc_exclude_non_revenue_payments():
    for kind in ("never", "trial_nc"):
        sql = _sql(svc.segment_filter(svc.Segment(kind=kind)))
        assert "NOT IN ('promo', 'test', 'referral_payout', 'admin')" in sql
    assert "trials" in _sql(svc.segment_filter(svc.Segment(kind="trial_nc")))


def test_ids_segment_ignores_opt_out():
    sql = _sql(svc.segment_filter(svc.Segment(kind="ids", ids=(5, 6))))
    assert "IN (5, 6)" in sql and "opt_out" not in sql


def test_legacy_names_still_work():
    assert svc.VALID_SEGMENTS == {"all", "active", "expired", "never"}
    with pytest.raises(ValueError):
        svc._segment_filter("ids")
    kb = svc._attach_unsub_button([{"text": "x", "url": "https://e.x"}])
    assert kb.inline_keyboard[-1][0].callback_data == svc.UNSUB_CALLBACK_DATA


async def test_sender_classifies_telegram_errors():
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
    from aiogram.methods import SendMessage

    from app.bot.broadcast_sender import AiogramBroadcastSender
    from tests.fakes.bot import make_bot

    bot, session = make_bot()
    sender = AiogramBroadcastSender(bot, retry_delay=0)
    ok = await sender.send(1, text_html="hi", photo_file_id=None, buttons=None, disable_notification=True)
    assert ok.status == "sent"
    session.fail["SendMessage"] = TelegramForbiddenError(method=SendMessage(chat_id=1, text="x"), message="blocked")
    assert (await sender.send(1, text_html="hi", photo_file_id=None, buttons=None,
                              disable_notification=True)).status == "blocked"
    session.fail["SendMessage"] = TelegramBadRequest(method=SendMessage(chat_id=1, text="x"), message="chat not found")
    assert (await sender.send(1, text_html="hi", photo_file_id=None, buttons=None,
                              disable_notification=True)).status == "blocked"
    session.fail["SendMessage"] = TelegramBadRequest(method=SendMessage(chat_id=1, text="x"), message="can't parse")
    assert (await sender.send(1, text_html="hi", photo_file_id=None, buttons=None,
                              disable_notification=True)).status == "failed"
