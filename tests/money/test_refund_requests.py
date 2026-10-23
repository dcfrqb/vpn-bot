"""24h refund requests: eligibility, one request per payment, idempotent admin decisions."""
from app.domain.models import AdminTopic
from app.services.fulfillment import Outcome
from tests.money.fakes import make_money

TG = 700003


async def _paid(m, deps, method="yookassa"):
    q = await m.checkout.quote(TG, "standard", 1)
    if method == "stars":
        res = await m.checkout.start_checkout(TG, q, method="stars")
        r = await m.fulfillment.on_stars_paid(telegram_id=TG, payment_id=res.intent.payment_id,
                                              charge_id="ch-1", total_amount=q.stars, currency="XTR")
    else:
        res = await m.checkout.start_checkout(TG, q)
        deps.payments.succeed(res.intent.external_id)
        r = await m.fulfillment.process(res.intent.payment_id, source="webhook")
    assert r.outcome is Outcome.FULFILLED
    return await deps.store.get(res.intent.payment_id)


async def test_request_needs_flag_owner_and_24h_window():
    m, deps = make_money(REFUND_24H_ENABLED=False)
    rec = await _paid(m, deps)
    assert await m.refunds.request(TG, rec.id) == "not_eligible"
    deps.settings.REFUND_24H_ENABLED = True
    assert await m.refunds.request(TG + 1, rec.id) == "not_eligible"
    deps.clock.advance(hours=25)
    assert await m.refunds.request(TG, rec.id) == "not_eligible"
    assert not deps.store.requests


async def test_one_request_per_payment_and_admin_gets_buttons():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    assert await m.refunds.request(TG, rec.id) == "requested"
    assert await m.refunds.request(TG, rec.id) == "already"
    alerts = deps.notifier.to_admins(AdminTopic.REFUNDS)
    assert len(alerts) == 1 and "не смог подключиться" in alerts[0].text


async def test_approve_refunds_money_revokes_access_once():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    deps.store.add_sub(TG, autorenew=True)
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    assert await m.refunds.decide(rid, 111, approve=True) == "done"
    assert deps.payments.refunds == [{"payment_id": rec.external_id, "amount": float(rec.amount),
                                      "key": f"rr:{rid}", "status": "succeeded"}]
    assert deps.provisioning.revokes == [(TG, f"refund_24h:{rid}")]
    assert deps.provisioning.revoke_payment_ids == [rec.id]
    assert (await deps.store.main_subscription(TG)).autorenew is False
    assert deps.store.requests[rid].status == "refunded"
    marker = (await deps.store.get(rec.id)).meta["refund_24h"]
    assert marker["state"] == "done" and marker["revoked"] is True
    assert (await m.refunds.decide(rid, 111, approve=True)).startswith("already:")
    assert (await m.refunds.decide(rid, 222, approve=False)).startswith("already:")
    assert len(deps.payments.refunds) == 1 and len(deps.provisioning.revokes) == 1
    assert any("Возврат одобрен" in s.text for s in deps.notifier.sent if s.kind == "user")


async def test_reject_tells_user_and_blocks_approval():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    assert await m.refunds.decide(rid, 111, approve=False) == "rejected"
    assert await m.refunds.decide(rid, 111, approve=True) == "already:rejected"
    assert not deps.payments.refunds and not deps.provisioning.revokes
    assert any("@crs_support" in s.text for s in deps.notifier.sent if s.kind == "user")


async def test_unknown_money_result_blocks_second_approval_and_requires_review():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    calls = []
    async def broken(*a, **kw):
        calls.append(a)
        return None
    deps.payments.refund = broken
    assert await m.refunds.decide(rid, 111, approve=True) == "unknown_money"
    assert deps.store.requests[rid].status == "approved" and not deps.provisioning.revokes
    assert await m.refunds.decide(rid, 111, approve=True) == "already:approved"
    assert len(calls) == 1


async def test_revoke_failure_is_reported_to_admin():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    deps.provisioning.revoke_result = False
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    assert await m.refunds.decide(rid, 111, approve=True) == "done_no_revoke"


async def test_stars_refund_uses_refund_star_payment():
    m, deps = make_money(REFUND_24H_ENABLED=True, STARS_ENABLED=True, STARS_RATE=1.0)
    rec = await _paid(m, deps, method="stars")
    assert rec.telegram_charge_id == "ch-1"
    assert await m.refunds.request(TG, rec.id) == "requested"
    rid = next(iter(deps.store.requests))
    assert await m.refunds.decide(rid, 111, approve=True) == "done"
    assert deps.stars.refunds == [(TG, "ch-1")] and not deps.payments.refunds
    assert (await deps.store.get(rec.id)).status == "refunded"
    assert any("Звезды вернулись" in s.text for s in deps.notifier.sent if s.kind == "user")


async def test_gift_and_autorenew_payments_are_not_eligible():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = deps.store.add_payment(TG, kind="gift", status="succeeded", plan_code="lite", period_months=1)
    assert await m.refunds.request(TG, rec.id) == "not_eligible"


# ----------------------------------------------------------------- review money M-1 / M-2


async def test_approve_takes_back_only_the_refunded_period():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    assert await m.refunds.decide(rid, 111, approve=True) == "done"
    assert deps.provisioning.revoke_months == [rec.period_months]


async def test_undelivered_payment_is_not_eligible_and_never_granted_after_refund():
    from app.services.payments.store import M_NEEDS_PROVISIONING, stuck_is_recoverable

    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = deps.store.add_payment(TG, kind="subscription", status="succeeded", plan_code="standard",
                                 period_months=1)
    await deps.store.patch_meta(rec.id, {M_NEEDS_PROVISIONING: True})
    assert await m.refunds.request(TG, rec.id) == "not_eligible"  # not delivered yet

    # Reverse race: a refund marker already on the row (approval in flight):
    # neither the recovery sweep nor a later process() grants it.
    await deps.store.patch_meta(rec.id, {"refund_24h": {"rid": 1, "state": "refunding"}})
    fresh = await deps.store.get(rec.id)
    assert not stuck_is_recoverable(fresh.meta, fresh.created_at, deps.clock().replace(tzinfo=None),
                                    __import__("datetime").timedelta(minutes=5))
    r = await m.fulfillment.process(rec.id, source="recovery")
    assert r.outcome is Outcome.REFUNDED
    assert not deps.provisioning.by_payment


async def test_approval_of_a_request_whose_payment_is_not_delivered_fails_safely():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    # simulate a row that lost its delivery marks (2.x data): approval refuses
    row = deps.store.payments[rec.id]
    row.subscription_id = None
    row.meta.pop("fulfilled_at", None)
    assert (await m.refunds.decide(rid, 111, approve=True)).startswith("failed:")
    assert not deps.payments.refunds and not deps.provisioning.revokes


async def test_refund_without_automatic_access_change_tells_user_about_manual_review():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    deps.provisioning.revoke_result = False
    assert await m.refunds.decide(rid, 42, approve=True) == "done_no_revoke"
    message = [s for s in deps.notifier.sent if s.kind == "user" and s.target == TG][-1].text
    assert "проверит поддержка" in message
    assert "отключен" not in message


async def _requested(m, deps):
    rec = await _paid(m, deps)
    await m.refunds.request(TG, rec.id)
    return rec, next(iter(deps.store.requests))


def _restart(deps):
    from app.services.money import build_money
    return build_money(deps)


async def test_failed_access_is_durable_and_restart_never_refunds_money_again():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    deps.provisioning.rollback_fail_times = 1
    assert await m.refunds.decide(rid, 111, approve=True) == "retry_access"
    marker = (await deps.store.get(rec.id)).meta["refund_24h"]
    assert marker["money_status"] == "succeeded" and marker["access"]["state"] == "retry"
    assert (await _restart(deps).refunds.recover_access())["checked"] == 0  # backoff
    deps.clock.advance(minutes=5)
    stats = await _restart(deps).refunds.recover_access()
    assert stats["applied"] == 1 and len(deps.payments.refunds) == 1
    assert deps.provisioning.rollback_calls == [(TG, 1, rec.id, f"rr:{rid}")] * 2
    assert (await deps.store.get(rec.id)).meta["refund_24h"]["revoked"] is True
    assert (await _restart(deps).refunds.recover_access())["checked"] == 0


async def test_crash_after_money_confirmation_before_access_call_recovers_from_metadata():
    import pytest
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    async def crash(pid):
        raise RuntimeError("process stopped before access")
    m.refunds.recover_one_access = crash
    with pytest.raises(RuntimeError):
        await m.refunds.decide(rid, 111, approve=True)
    assert len(deps.payments.refunds) == 1 and not deps.provisioning.rollback_calls
    stats = await _restart(deps).refunds.recover_access()
    assert stats["applied"] == 1 and len(deps.payments.refunds) == 1


async def test_lost_access_response_and_failed_bypass_retry_debit_only_once():
    for failure in ("rollback_response_fail_times", "rollback_sync_fail_times"):
        m, deps = make_money(REFUND_24H_ENABLED=True)
        rec, rid = await _requested(m, deps)
        setattr(deps.provisioning, failure, 1)
        assert await m.refunds.decide(rid, 111, approve=True) == "retry_access"
        deps.clock.advance(minutes=5)
        assert (await _restart(deps).refunds.recover_access())["applied"] == 1
        assert len(deps.provisioning.revokes) == 1 and len(deps.payments.refunds) == 1
        assert len(deps.provisioning.rollback_calls) == 2
        notices = [s for s in deps.notifier.sent if s.kind == "user" and (s.dedup_key or "").startswith("rr_notice:")]
        assert len(notices) == 2 and "автоматически" in notices[0].text


async def test_pending_provider_refund_does_not_revoke_until_confirmed():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    original = deps.payments.refund
    async def pending(*a, **kw):
        result = await original(*a, **kw)
        result.update(id="pending-refund", status="pending")
        return result
    deps.payments.refund = pending
    assert await m.refunds.decide(rid, 111, approve=True) == "pending_money"
    assert not deps.provisioning.rollback_calls and deps.store.requests[rid].status == "approved"
    assert not any("возврат денег подтвержден" in s.text.lower() for s in deps.notifier.sent)
    deps.clock.advance(minutes=5)
    assert (await _restart(deps).refunds.recover_access())["awaiting_money"] == 1
    assert not deps.provisioning.rollback_calls
    deps.payments.refunds[0]["status"] = "succeeded"
    deps.clock.advance(minutes=10)
    assert (await _restart(deps).refunds.recover_access())["applied"] == 1
    assert deps.store.requests[rid].status == "refunded" and len(deps.payments.refunds) == 1
    assert deps.payments.refund_reads == ["pending-refund"] * 2


async def test_canceled_pending_refund_goes_to_manual_without_access_debit():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    original = deps.payments.refund
    async def pending(*a, **kw):
        result = await original(*a, **kw)
        result.update(id="pending-refund", status="pending")
        return result
    deps.payments.refund = pending
    await m.refunds.decide(rid, 111, approve=True)
    deps.payments.refunds[0]["status"] = "canceled"
    deps.clock.advance(minutes=5)
    assert (await _restart(deps).refunds.recover_access())["manual_review"] == 1
    assert not deps.provisioning.rollback_calls and len(deps.payments.refunds) == 1
    assert (await _restart(deps).refunds.recover_access())["checked"] == 0


async def test_manual_skip_and_no_account_are_terminal_and_alert_once():
    for action in ("skipped", "no_account"):
        m, deps = make_money(REFUND_24H_ENABLED=True)
        rec, rid = await _requested(m, deps)
        deps.provisioning.revoke_result = False
        deps.provisioning.rollback_action = action
        assert await m.refunds.decide(rid, 111, approve=True) == "done_no_revoke"
        assert (await _restart(deps).refunds.recover_access())["checked"] == 0
        alerts = [s for s in deps.notifier.to_admins() if s.dedup_key == f"rr_alert:{rid}:manual_review"]
        assert len(alerts) == 1 and len(deps.payments.refunds) == 1


async def test_legacy_failed_access_requires_verified_money_evidence():
    for confirmed in (False, True):
        m, deps = make_money(REFUND_24H_ENABLED=True)
        rec, rid = await _requested(m, deps)
        await deps.store.transition_refund_request(rid, ("pending",), "refunded")
        await deps.store.patch_meta(rec.id, {"refund_24h": {"rid": rid, "state": "done", "revoked": False}})
        if confirmed:
            deps.payments.payments[rec.external_id]["refunded_amount"] = float(rec.amount)
        stats = await _restart(deps).refunds.recover_access()
        assert stats["applied" if confirmed else "manual_review"] == 1
        assert bool(deps.provisioning.rollback_calls) is confirmed and not deps.payments.refunds


async def test_legacy_stars_approved_marker_does_not_infer_money_success():
    m, deps = make_money(REFUND_24H_ENABLED=True, STARS_ENABLED=True, STARS_RATE=1.0)
    rec = await _paid(m, deps, method="stars")
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    await deps.store.transition_refund_request(rid, ("pending",), "approved")
    await deps.store.patch_meta(rec.id, {"refund_24h": {"rid": rid, "state": "refunding"}})
    assert (await _restart(deps).refunds.recover_access())["manual_review"] == 1
    assert not deps.stars.refunds and not deps.provisioning.rollback_calls


async def test_missing_paid_period_never_becomes_full_cut_during_recovery():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    deps.store.payments[rec.id].period_months = None
    assert await m.refunds.decide(rid, 111, approve=True) == "done_no_revoke"
    assert not deps.provisioning.rollback_calls


async def test_access_lease_survives_restart_and_stale_owner_cannot_finish():
    from datetime import timedelta
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    await deps.store.transition_refund_request(rid, ("pending",), "approved")
    await deps.store.update_refund_marker(rec.id, {"rid": rid, "state": "done", "money_status": "succeeded"},
                                         access_patch={"state": "pending"})
    assert await deps.store.claim_refund_access(rec.id, owner="crashed", now=deps.clock(), lease_for=timedelta(minutes=10))
    assert (await _restart(deps).refunds.recover_access())["checked"] == 0
    deps.clock.advance(minutes=11)
    assert (await _restart(deps).refunds.recover_access())["applied"] == 1
    assert await deps.store.finish_refund_access(rec.id, owner="crashed", state="retry", now=deps.clock()) is None
    assert (await deps.store.get(rec.id)).meta["refund_24h"]["access"]["state"] == "applied"


async def test_concurrent_workers_and_stale_money_patch_preserve_completed_access():
    import asyncio
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    await deps.store.transition_refund_request(rid, ("pending",), "approved")
    await deps.store.update_refund_marker(rec.id, {"rid": rid, "state": "done", "money_status": "succeeded"},
                                         access_patch={"state": "pending"})
    states = await asyncio.gather(*[_restart(deps).refunds.recover_one_access(rec.id) for _ in range(4)])
    assert states.count("applied") == 1 and len(deps.provisioning.revokes) == 1
    await deps.store.update_refund_marker(rec.id, {"state": "pending_money", "money_status": "pending"},
                                         access_patch={"state": "awaiting_money"})
    marker = (await deps.store.get(rec.id)).meta["refund_24h"]
    assert marker["money_status"] == "succeeded" and marker["access"]["state"] == "applied"
    assert marker["revoked"] is True and marker["state"] == "done"


async def test_unknown_stars_response_never_calls_money_refund_twice():
    m, deps = make_money(REFUND_24H_ENABLED=True, STARS_ENABLED=True, STARS_RATE=1.0)
    rec = await _paid(m, deps, method="stars")
    await m.refunds.request(TG, rec.id)
    rid = next(iter(deps.store.requests))
    original = deps.stars.refund
    async def lost(*a):
        await original(*a)
        raise TimeoutError("Telegram reply lost")
    deps.stars.refund = lost
    assert await m.refunds.decide(rid, 111, approve=True) == "unknown_money"
    assert await m.refunds.decide(rid, 111, approve=True) == "already:approved"
    assert (await _restart(deps).refunds.recover_access())["checked"] == 0
    marker = (await deps.store.get(rec.id)).meta["refund_24h"]
    assert marker["access"]["reason_class"] == "money_unconfirmed"
    assert len(deps.stars.refunds) == 1 and not deps.provisioning.rollback_calls


async def test_lost_yookassa_refund_response_is_verified_without_second_money_call():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    original = deps.payments.refund
    async def lost(*a, **kw):
        await original(*a, **kw)
        raise TimeoutError("provider reply lost")
    deps.payments.refund = lost
    assert await m.refunds.decide(rid, 111, approve=True) == "done"
    assert len(deps.payments.refunds) == 1 and len(deps.provisioning.revokes) == 1


async def test_unavailable_money_read_never_says_money_is_confirmed():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    await deps.store.transition_refund_request(rid, ("pending",), "approved")
    await deps.store.update_refund_marker(rec.id, {"rid": rid, "state": "done", "revoked": False})
    async def unavailable(*a):
        return None
    deps.payments.get_payment = unavailable
    assert (await _restart(deps).refunds.recover_access())["retry"] == 1
    notices = [s.text.lower() for s in deps.notifier.sent if (s.dedup_key or "").startswith("rr_notice:")]
    assert notices and not any("денег подтвержден" in text for text in notices)
    assert not deps.provisioning.rollback_calls and not deps.payments.refunds


async def test_applied_access_notice_false_delivery_retries_only_notice_after_restart():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    original = deps.notifier.notify_user
    async def unavailable(*a, **kw):
        return False
    deps.notifier.notify_user = unavailable
    assert await m.refunds.decide(rid, 111, approve=True) == "done"
    marker = (await deps.store.get(rec.id)).meta["refund_24h"]
    assert marker["access"]["state"] == "applied" and marker["access"]["notice_pending"]
    deps.notifier.notify_user = original
    deps.clock.advance(minutes=5)
    assert (await _restart(deps).refunds.recover_access())["notified"] == 1
    assert len(deps.payments.refunds) == 1 and len(deps.provisioning.rollback_calls) == 1
    assert (await _restart(deps).refunds.recover_access())["checked"] == 0
    assert len([s for s in deps.notifier.sent if s.dedup_key == f"rr_notice:{rid}:applied"]) == 1


async def test_manual_alert_delivery_retries_without_new_access_or_user_notice():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    deps.provisioning.revoke_result = False
    original = deps.notifier.notify_admins
    async def unavailable(*a, **kw):
        return 0
    deps.notifier.notify_admins = unavailable
    assert await m.refunds.decide(rid, 111, approve=True) == "done_no_revoke"
    deps.notifier.notify_admins = original
    deps.clock.advance(minutes=5)
    assert (await _restart(deps).refunds.recover_access())["notified"] == 1
    assert len(deps.payments.refunds) == 1 and len(deps.provisioning.rollback_calls) == 1
    assert len([s for s in deps.notifier.sent if s.dedup_key == f"rr_notice:{rid}:manual_review"]) == 1
    assert len([s for s in deps.notifier.sent if s.dedup_key == f"rr_alert:{rid}:manual_review"]) == 1


async def test_verified_money_racing_stale_provider_read_cannot_close_queue():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    await deps.store.transition_refund_request(rid, ("pending",), "approved")
    await deps.store.update_refund_marker(rec.id, {"rid": rid, "state": "refunding", "money_status": "unknown"},
                                         access_patch={"state": "pending"})
    original = m.refunds._money_confirmed
    async def stale(read):
        await deps.store.update_refund_marker(rec.id, {"money_status": "succeeded", "state": "done"})
        return "manual_review"
    m.refunds._money_confirmed = stale
    assert await m.refunds.recover_one_access(rec.id) == "retry"
    marker = (await deps.store.get(rec.id)).meta["refund_24h"]
    assert marker["money_status"] == "succeeded" and marker["access"]["state"] == "pending"
    m.refunds._money_confirmed = original
    assert (await _restart(deps).refunds.recover_access())["applied"] == 1
    assert len(deps.provisioning.revokes) == 1 and not deps.payments.refunds


async def test_delayed_successful_money_response_rearms_worker_money_review():
    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec, rid = await _requested(m, deps)
    original = deps.payments.refund
    async def delayed(*a, **kw):
        deps.clock.advance(minutes=6)
        assert (await _restart(deps).refunds.recover_access())["manual_review"] == 1
        return await original(*a, **kw)
    deps.payments.refund = delayed
    assert await m.refunds.decide(rid, 111, approve=True) == "done"
    marker = (await deps.store.get(rec.id)).meta["refund_24h"]
    assert marker["access"]["state"] == "applied" and len(deps.provisioning.revokes) == 1
    assert len(deps.payments.refunds) == 1


async def test_refund_decision_reuses_bound_card_after_restart():
    from types import SimpleNamespace
    from app.services.notifications import TelegramNotifier
    from tests.fakes.bot import make_bot

    for approve in [False, True]:
        m, deps = make_money(REFUND_24H_ENABLED=True)
        rec = await _paid(m, deps)
        bot, calls = make_bot()
        deps.notifier = TelegramNotifier(bot, SimpleNamespace(ADMINS=[111], ADMIN_CHAT_ID=None))
        assert await m.refunds.request(TG, rec.id, message_id=77) == 'requested'
        rid = next(iter(deps.store.requests))
        calls.calls.clear()
        result = await _restart(deps).refunds.decide(rid, 111, approve=approve)
        assert result.startswith('done') if approve else result == 'rejected'
        assert len(calls.calls_of('EditMessageText')) == 1
        assert calls.calls_of('EditMessageText')[0].params['message_id'] == 77
        assert not [c for c in calls.calls_of('SendMessage') if c.chat_id == TG]
        await _restart(deps).refunds.decide(rid, 111, approve=approve)
        await _restart(deps).refunds.recover_access()
        assert len(calls.calls_of('EditMessageText')) == 1
        assert not [c for c in calls.calls_of('SendMessage') if c.chat_id == TG]
        assert len(deps.payments.refunds) == int(approve)


async def test_bound_refund_notice_timeout_retries_edit_without_refunding_again():
    from types import SimpleNamespace
    from aiogram.exceptions import TelegramNetworkError, TelegramBadRequest
    from aiogram.methods import EditMessageText
    from app.services.notifications import TelegramNotifier
    from tests.fakes.bot import make_bot

    m, deps = make_money(REFUND_24H_ENABLED=True)
    rec = await _paid(m, deps)
    bot, calls = make_bot()
    deps.notifier = TelegramNotifier(bot, SimpleNamespace(ADMINS=[111], ADMIN_CHAT_ID=None))
    await m.refunds.request(TG, rec.id, message_id=88)
    rid = next(iter(deps.store.requests))
    calls.calls.clear()
    method = EditMessageText(chat_id=TG, message_id=88, text='result')
    calls.fail['EditMessageText'] = TelegramNetworkError(method=method, message='timeout after delivery')
    assert await m.refunds.decide(rid, 111, approve=True) == 'done'
    calls.fail['EditMessageText'] = TelegramBadRequest(method=method, message='message is not modified')
    deps.clock.advance(minutes=5)
    assert (await _restart(deps).refunds.recover_access())['notified'] == 1
    assert len(deps.payments.refunds) == 1 and len(deps.provisioning.rollback_calls) == 1
    assert len(calls.calls_of('EditMessageText')) == 2
    assert not [c for c in calls.calls_of('SendMessage') if c.chat_id == TG]
