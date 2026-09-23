"""Callback matrix: every callback the bot can receive resolves to exactly one handler.

Resolution is computed on the REAL dispatcher by evaluating router and
handler filters (no handler is executed):

  * a packed 3.0 callback (sample of every class in app.bot.callbacks) must be
    accepted by exactly ONE handler of the new routers - unless its owner
    stream has not landed yet (PENDING_PACKED). A stream that ships the
    handler removes the entry; the test fails if an entry is stale;
  * a 2.x string (LEGACY_SAMPLES) must map to the expected alias key and
    packed rewrite, and then resolve to exactly one handler: the new one when
    a new handler accepts the rewrite, otherwise exactly one specific 2.x
    handler, or (no specific handler) the 2.x catch-all legacy_callbacks.
"""
import pytest

from app.bot import callbacks as cb
from app.bot.legacy_aliases import NATIVE_LEGACY_STRINGS, find_alias, rewrite
from app.bot.routers import NEW_ROUTER_MODULES
from tests.fakes.bot import callback_update

CATCH_ALL = ("legacy_callbacks", "legacy_callback_handler")

# One worst-case-ish sample per packed class/area -> owner stream.
PACKED_SAMPLES = {
    cb.Nav(s="main").pack(): "D",
    cb.Nav(s="plans").pack(): "A",
    cb.Nav(s="connect").pack(): "D",
    cb.Nav(s="help").pack(): "D",
    cb.Plan(c="pro").pack(): "A",
    cb.Period(c="pro", m=12).pack(): "A",
    cb.PayCheck(pid=123).pack(): "A",
    cb.PayStars(c="pro", m=1).pack(): "A",
    cb.AutoPay(a="off").pack(): "A",
    cb.Dev(a="list").pack(): "D",
    cb.RefundReq(pid=123).pack(): "A",
    cb.AdmRefund(a="ok", rid=5).pack(): "A",
    cb.AdmReview(a="ok", pid=5).pack(): "A",
    cb.PromoAct(a="enter").pack(): "E",
    cb.PromoAdm(a="list").pack(): "E",
    cb.Adm(s="panel", a="open").pack(): "E",
    cb.BcAdm(a="new").pack(): "E",
    cb.Gift(a="buy").pack(): "A",
}
# Streams delete their entries when their handlers land.
PENDING_PACKED = set(PACKED_SAMPLES) - {
    # stream E
    cb.PromoAct(a="enter").pack(), cb.PromoAdm(a="list").pack(), cb.Adm(s="panel", a="open").pack(),
    cb.BcAdm(a="new").pack(),
}

# 2.x strings (every producer in the 2.1.1 code) -> (alias key, packed rewrite or None).
LEGACY_SAMPLES = {
    "back_to_main": ("back_to_main", "n:main:"),
    "buy_subscription": ("buy_subscription", "n:plans:"),
    "connect_vpn": ("connect_vpn", "n:connect:"),
    "get_subscription_link": ("get_subscription_link", "n:connect:link"),
    "help": ("help", "n:help:"),
    "refresh_info": ("refresh_info", "n:main:refresh"),
    "my_plan": ("my_plan", "n:plan:"),
    "friend_request_yes": ("friend_request", "n:friend_req:yes"),
    "friend_request_no": ("friend_request", "n:friend_req:no"),
    "plan_basic": ("plan", "pl:basic"),
    "plan_premium": ("plan", "pl:premium"),
    "plan_lite": ("plan", "pl:lite"),
    "plan_basic_1": ("plan_period", "pe:basic:1"),
    "plan_premium_12": ("plan_period", "pe:premium:12"),
    "pay_yookassa_pro_1": ("pay_yookassa", "pe:pro:1"),
    "pay_yookassa_basic_3_249": ("pay_yookassa", "pe:basic:3"),
    "pay_yookassa_premium": ("pay_yookassa", "pe:premium:1"),
    "pay_yookassa_obhod_250_1": ("pay_yookassa_other", None),
    "check_payment:2f8a1c3e-000f-5000-9000-1b2c3d4e5f60": (
        "check_payment", "pc:0:2f8a1c3e-000f-5000-9000-1b2c3d4e5f60"),
    "ui:main_menu:back:-": ("ui", "n:main_menu:back"),
    "ui:main_menu:refresh:-": ("ui", "n:main_menu:refresh"),
    "ui:subscription_plans:open:-": ("ui", "n:subscription_plans:open"),
    "ui:subscription_plans:select:pro&12": ("ui", "n:subscription_plans:select.pro&12"),
    "ui:subscription_plans:obhod:-": ("ui", "n:subscription_plans:obhod"),
    "ui:subscription_plan_detail:back:-": ("ui", "n:subscription_plan_detail:back"),
    "ui:connect:open:-": ("ui", "n:connect:open"),
    "ui:help:open:-": ("ui", "n:help:open"),
    "ui:admin_panel:open:-": ("ui", "n:admin_panel:open"),
    "ui:admin_payments:page:2&all": ("ui", "n:admin_payments:page.2&all"),
    "ui:admin_users:page:3": ("ui", "n:admin_users:page.3"),
    "admin_panel": ("admin_panel", "ad:panel:open:"),
    "admin_stats": ("admin_stats", "ad:stats:open:"),
    "admin_users": ("admin_users", "ad:users:open:"),
    "admin_payments": ("admin_payments", "ad:payments:open:"),
    "admin_back": ("admin_back", "ad:panel:back:"),
    "admin_users_page_2": ("admin_users_page", "ad:users:page:2"),
    "admin_payments_all": ("admin_payments_filter", "ad:payments:filter:all"),
    "admin_payments_succeeded": ("admin_payments_filter", "ad:payments:filter:succeeded"),
    "admin_payments_pending": ("admin_payments_filter", "ad:payments:filter:pending"),
    "admin_payments_page_2_all": ("admin_payments_page", "ad:payments:page:2.all"),
    "admin_payments_page_3": ("admin_payments_page", "ad:payments:page:3.all"),
    "admin_grant_17_premium_3": ("admin_grant", "ad:access:grant:17.premium.3"),
    "admin_grant_forever_17": ("admin_grant_forever", "ad:access:forever:17"),
    "admin_reject_17": ("admin_reject", "ad:access:reject:17"),
    "friend_grant_1m_900000101": ("friend_grant", "ad:friend:grant_1m:900000101"),
    "friend_grant_forever_900000101": ("friend_grant", "ad:friend:grant_forever:900000101"),
    "friend_reject_900000101": ("friend_reject", "ad:friend:reject:900000101"),
    "admin_promo_grant_3m_900000101": ("admin_promo_grant", "ad:promo_req:grant_3m:900000101"),
    "admin_promo_reject_900000101": ("admin_promo_reject", "ad:promo_req:reject:900000101"),
    "rv_ok:42": ("review", "av:ok:42"),
    "rv_no:42": ("review", "av:no:42"),
    "sitelogin:confirm:abc": ("sitelogin", None),
}

# 2.x overlaps that exist in 2.1.1 (aiogram takes the first registered one).
# Listed so the matrix stays honest; cutover removes them with the old routers.
KNOWN_LEGACY_OVERLAPS = {
    "admin_grant_forever_17": ("admin", "admin_grant_forever"),
}

NEW_NAMES = {m.rsplit(".", 1)[-1] for m in NEW_ROUTER_MODULES}


async def _accepting(dp, bot, data):
    event = callback_update(bot, _user(), data).callback_query
    kwargs = {"bot": bot, "raw_state": None, "event_from_user": event.from_user}
    out = []
    for router in dp.chain_tail:
        if router is dp:
            continue
        obs = router.callback_query
        ok, _ = await obs.check_root_filters(event, **kwargs)
        if not ok:
            continue
        for h in obs.handlers:
            accepted, _ = await h.check(event, **kwargs)
            if accepted:
                out.append((router.name, h.callback.__name__))
    return out


def _user():
    from tests.fakes.bot import user

    return user()


def _is_new(router_name: str) -> bool:
    return router_name.startswith("r3_")


async def test_every_packed_callback_resolves_to_exactly_one_new_handler(flow):
    problems = []
    for data, owner in PACKED_SAMPLES.items():
        found = [h for h in await _accepting(flow.dp, flow.bot, data) if _is_new(h[0])]
        if data in PENDING_PACKED:
            if found:
                problems.append(f"{data}: handled by {found}, remove it from PENDING_PACKED (stream {owner})")
        elif len(found) != 1:
            problems.append(f"{data}: expected 1 new handler, got {found}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("legacy", sorted(LEGACY_SAMPLES))
async def test_every_legacy_string_maps_to_expected_alias(legacy):
    key, packed = LEGACY_SAMPLES[legacy]
    found = find_alias(legacy)
    assert found is not None, f"{legacy}: no alias"
    assert found[0].key == key
    assert rewrite(legacy) == packed


@pytest.mark.parametrize("legacy", sorted(LEGACY_SAMPLES) + list(NATIVE_LEGACY_STRINGS))
async def test_every_legacy_string_resolves_to_exactly_one_handler(flow, legacy):
    packed = rewrite(legacy)
    if packed:
        new = [h for h in await _accepting(flow.dp, flow.bot, packed) if _is_new(h[0])]
        assert len(new) <= 1, f"{legacy} -> {packed}: several new handlers {new}"
        if new:
            return  # the alias middleware hands it to that one new handler
    found = await _accepting(flow.dp, flow.bot, legacy)
    if legacy in NATIVE_LEGACY_STRINGS:
        # bc:unsub / bc:close are valid packed Bc callbacks: the new Bc handler
        # (stream E) takes them over directly, exactly one new handler.
        new = [h for h in found if _is_new(h[0])]
        assert len(new) == 1, f"{legacy}: expected one new Bc handler, got {new}"
        return
    assert not [h for h in found if _is_new(h[0])], "a new handler must not take raw 2.x strings"
    specific = [h for h in found if h != CATCH_ALL]
    if legacy in KNOWN_LEGACY_OVERLAPS:
        assert specific[0] == KNOWN_LEGACY_OVERLAPS[legacy]
        return
    assert len(specific) <= 1, f"{legacy}: several 2.x handlers {specific}"
    if not specific:
        assert CATCH_ALL in found


def test_packed_samples_cover_every_callback_class():
    prefixes = {k.split(":", 1)[0] for k in PACKED_SAMPLES}
    missing = [c.__name__ for c in cb.ALL_CALLBACKS if c.__prefix__ not in prefixes and c is not cb.Bc]
    assert not missing
