"""CheckoutService: server prices, pending reuse 15 min, stale quotes, Stars, gifts, stop-list."""
from dataclasses import replace

from app.domain.models import PaymentKind
from app.domain.plans import get_plan_price
from app.services.checkout import parse_stars_payload, stars_payload
from app.services.payments.pricing import stars_for_rub
from tests.money.fakes import make_money

TG = 700002


async def test_quote_is_catalog_price_and_legacy_only_for_owner():
    m, deps = make_money()
    q = await m.checkout.quote(TG, "pro", 12)
    assert q.amount_rub == get_plan_price("pro", 12) and q.stars is None and not q.is_legacy
    assert await m.checkout.quote(TG, "trial", 1) is None
    assert await m.checkout.quote(TG, "pro", 2) is None
    assert await m.checkout.quote(TG, "basic", 1) is None
    deps.hooks.last_plans[TG] = "basic"
    assert (await m.checkout.quote(TG, "basic", 1)).is_legacy
    assert await m.checkout.quote(TG, "basic", 1, gift=True) is None  # gifts: menu plans only


async def test_stars_price_only_when_enabled_and_rate_set():
    m, _ = make_money(STARS_ENABLED=True, STARS_RATE=1.5)
    q = await m.checkout.quote(TG, "lite", 1)
    assert q.stars == stars_for_rub(get_plan_price("lite", 1), 1.5) and q.stars * 1.5 >= q.amount_rub
    m2, _ = make_money(STARS_ENABLED=True, STARS_RATE=0)
    assert (await m2.checkout.quote(TG, "lite", 1)).stars is None


async def test_start_creates_one_payment_and_reuses_it_for_15_minutes():
    m, deps = make_money()
    q = await m.checkout.quote(TG, "standard", 3)
    first = await m.checkout.start_checkout(TG, q, user={"username": "u", "first_name": "F"})
    assert first.ok and not first.reused and first.intent.confirmation_url
    rec = await deps.store.get(first.intent.payment_id)
    assert rec.amount == get_plan_price("standard", 3) and rec.plan_code == "standard" and rec.period_months == 3
    assert rec.kind == "subscription" and rec.method == "yookassa"
    created = deps.payments.payments[rec.external_id]
    assert created["amount"] == get_plan_price("standard", 3)
    assert created["metadata"]["tg_user_id"] == TG and created["metadata"]["expected_amount"] == rec.amount

    deps.clock.advance(minutes=10)
    again = await m.checkout.start_checkout(TG, q)
    assert again.reused and again.intent.payment_id == rec.id and again.intent.confirmation_url == rec.confirmation_url
    other = await m.checkout.start_checkout(TG, await m.checkout.quote(TG, "standard", 1))
    assert other.intent.payment_id != rec.id  # another period = another payment

    deps.clock.advance(minutes=6)
    later = await m.checkout.start_checkout(TG, q)
    assert not later.reused and later.intent.payment_id not in (rec.id, other.intent.payment_id)


async def test_paid_payment_is_not_reused():
    m, deps = make_money()
    q = await m.checkout.quote(TG, "lite", 1)
    first = await m.checkout.start_checkout(TG, q)
    await deps.store.mark_paid(first.intent.payment_id)
    second = await m.checkout.start_checkout(TG, q)
    assert second.intent.payment_id != first.intent.payment_id


async def test_forged_or_stale_quote_is_refused():
    m, deps = make_money()
    q = await m.checkout.quote(TG, "pro", 12)
    res = await m.checkout.start_checkout(TG, replace(q, amount_rub=1))
    assert res.error == "unavailable" and not deps.payments.payments
    res = await m.checkout.start_checkout(TG, replace(q, plan_code="basic"))
    assert res.error == "unavailable"


async def test_blocked_user_cannot_pay_and_admin_is_told_once():
    m, deps = make_money()
    deps.hooks.blocked_users[TG] = "chargeback"
    q = await m.checkout.quote(TG, "lite", 1)
    assert (await m.checkout.start_checkout(TG, q)).error == "blocked"
    assert (await m.checkout.start_checkout(TG, q)).error == "blocked"
    assert len(deps.notifier.to_admins()) == 1 and not deps.payments.payments


async def test_provider_failure_is_create_failed_without_row():
    m, deps = make_money()
    deps.payments.fail_create = True
    res = await m.checkout.start_checkout(TG, await m.checkout.quote(TG, "lite", 1))
    assert res.error == "create_failed" and not deps.store.payments


async def test_autorenew_ignored_when_flag_off():
    m, deps = make_money(AUTOPAY_ENABLED=False)
    res = await m.checkout.start_checkout(TG, await m.checkout.quote(TG, "lite", 1), autorenew=True)
    assert not res.intent.autorenew
    assert deps.payments.payments[res.intent.external_id]["save_payment_method"] is False


async def test_stars_checkout_records_expected_stars_and_sends_invoice():
    m, deps = make_money(STARS_ENABLED=True, STARS_RATE=1.0)
    q = await m.checkout.quote(TG, "lite", 1)
    res = await m.checkout.start_checkout(TG, q, method="stars")
    assert res.ok and res.intent.stars == q.stars
    rec = await deps.store.get(res.intent.payment_id)
    assert rec.provider == "stars" and rec.currency == "XTR" and rec.expected_stars == q.stars
    assert deps.stars.invoices == [{"chat_id": TG, "stars": q.stars, "title": "CRS VPN Lite",
                                    "payload": stars_payload(rec.id)}]
    again = await m.checkout.start_checkout(TG, q, method="stars")
    assert again.reused and len(deps.stars.invoices) == 2  # same row, invoice re-sent


async def test_stars_disabled_refuses_stars_checkout():
    m, _ = make_money(STARS_ENABLED=False)
    q = await m.checkout.quote(TG, "lite", 1)
    assert (await m.checkout.start_checkout(TG, q, method="stars")).error == "stars_disabled"


async def test_precheck_stars():
    m, deps = make_money(STARS_ENABLED=True, STARS_RATE=1.0)
    q = await m.checkout.quote(TG, "lite", 1)
    res = await m.checkout.start_checkout(TG, q, method="stars")
    payload = stars_payload(res.intent.payment_id)
    assert await m.checkout.precheck_stars(TG, payload, q.stars, "XTR") is None
    assert await m.checkout.precheck_stars(TG, payload, q.stars - 1, "XTR") is not None
    assert await m.checkout.precheck_stars(TG + 1, payload, q.stars, "XTR") is not None
    assert await m.checkout.precheck_stars(TG, "p:999", q.stars, "XTR") is not None
    deps.settings.STARS_RATE = 2.0  # price in stars changed since the invoice
    assert await m.checkout.precheck_stars(TG, payload, q.stars, "XTR") is not None


def test_stars_payload_roundtrip():
    assert parse_stars_payload(stars_payload(42)) == 42
    assert parse_stars_payload("x:1") is None and parse_stars_payload("p:abc") is None


async def test_gift_checkout_is_a_separate_payment_kind():
    m, deps = make_money(GIFTS_ENABLED=True, AUTOPAY_ENABLED=True)
    q = await m.checkout.quote(TG, "standard", 3, gift=True)
    res = await m.checkout.start_checkout(TG, q, kind="gift", autorenew=True)
    assert res.intent.kind is PaymentKind.GIFT and not res.intent.autorenew
    rec = await deps.store.get(res.intent.payment_id)
    assert rec.kind == "gift" and deps.payments.payments[rec.external_id]["description"].startswith("Подарок")


async def test_plan_and_period_options():
    m, deps = make_money()
    plans = await m.checkout.plan_options(TG)
    assert [p[0] for p in plans] == ["lite", "standard", "pro"]
    deps.hooks.last_plans[TG] = "premium"
    assert [p[0] for p in await m.checkout.plan_options(TG)][-1] == "premium"
    assert "premium" not in [p[0] for p in await m.checkout.plan_options(TG, gift=True)]
    periods = await m.checkout.period_options(TG, "pro")
    assert [p[0] for p in periods] == [1, 3, 6, 12]
    assert periods[0][2] == 0 and periods[-1][2] > 0


async def test_port_check_maps_statuses():
    from app.domain.models import PaymentStatus

    m, deps = make_money()
    res = await m.checkout.start_checkout(TG, await m.checkout.quote(TG, "lite", 1))
    assert (await m.checkout.check(TG, res.intent.payment_id)).status is PaymentStatus.PENDING
    deps.payments.succeed(res.intent.external_id)
    assert (await m.checkout.check(TG, res.intent.payment_id)).status is PaymentStatus.SUCCEEDED
    assert (await m.checkout.check(TG + 1, res.intent.payment_id)).status is PaymentStatus.FAILED


async def test_stoplisted_after_invoice_is_refused_at_precheck_and_held_if_paid():
    """Security m-6: blocked between the invoice and the payment."""
    from app.domain.texts import checkout as T
    from app.services.fulfillment import Outcome

    m, deps = make_money(STARS_ENABLED=True, STARS_RATE=1.0)
    q = await m.checkout.quote(TG, "lite", 1)
    res = await m.checkout.start_checkout(TG, q, method="stars")
    payload = stars_payload(res.intent.payment_id)
    deps.hooks.blocked_users[TG] = "конкурент"
    assert await m.checkout.precheck_stars(TG, payload, q.stars, "XTR") == T.PAYMENT_BLOCKED
    # Telegram charged anyway (race): recorded, held for an admin, nothing granted.
    r = await m.fulfillment.on_stars_paid(telegram_id=TG, payment_id=res.intent.payment_id,
                                          charge_id="ch-9", total_amount=q.stars, currency="XTR")
    assert r.outcome is Outcome.HELD
    rec = await deps.store.get(res.intent.payment_id)
    assert rec.status == "succeeded" and rec.meta.get("needs_review")
    assert not deps.provisioning.by_payment
