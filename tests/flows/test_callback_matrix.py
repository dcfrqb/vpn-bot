"""Callback matrix: every callback the bot can receive resolves to exactly one handler.

Resolution is computed on the REAL dispatcher by evaluating router and
handler filters (no handler is executed). After the 3.0 cutover there are no
2.x UI routers left:

  * a packed 3.0 callback (sample of every class in app.bot.callbacks) must be
    accepted by exactly ONE specific handler (the ``r3_fallback`` catch-all
    does not count);
  * a 2.x string still sitting in users' chats (LEGACY_SAMPLES: every string
    the 2.1.1 code ever produced) must map to the expected alias key and
    packed rewrite, and the rewrite must reach exactly ONE specific handler.
    Only the strings in BY_DESIGN_FALLBACK land on the fallback (answer +
    main menu); ``sitelogin:*`` goes to the site_login router untouched.
"""
import pytest

from app.bot import callbacks as cb
from app.bot.legacy_aliases import NATIVE_LEGACY_STRINGS, find_alias, rewrite
from app.bot.routers import NEW_ROUTER_MODULES
from tests.fakes.bot import callback_update

FALLBACK = "r3_fallback"
SITE_LOGIN = "site_login"

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
    # the rest of the 2.1.1 producers (keyboards, ScreenManager, admin, payments)
    "plan_premium_6": ("plan_period", "pe:premium:6"),
    "check_payment": ("check_payment_bare", None),
    "ui:main_menu:open:-": ("ui", "n:main_menu:open"),
    "ui:subscription_plans:back:-": ("ui", "n:subscription_plans:back"),
    "ui:subscription_plans:extend:-": ("ui", "n:subscription_plans:extend"),
    "ui:subscription_plans:select:pro": ("ui", "n:subscription_plans:select.pro"),
    "ui:subscription_plans:buy_obhod:obhod_250": ("ui", "n:subscription_plans:buy_obhod.obhod_250"),
    "ui:connect:back:-": ("ui", "n:connect:back"),
    "ui:connect_success:back:-": ("ui", "n:connect_success:back"),
    "ui:help:back:-": ("ui", "n:help:back"),
    "ui:profile:back:-": ("ui", "n:profile:back"),
    "ui:error:back:-": ("ui", "n:error:back"),
    "ui:subscription:back:-": ("ui", "n:subscription:back"),
    "ui:subscription_payment:back:-": ("ui", "n:subscription_payment:back"),
    "ui:admin_panel:refresh:-": ("ui", "n:admin_panel:refresh"),
    "ui:admin_stats:refresh:-": ("ui", "n:admin_stats:refresh"),
    "ui:admin_users:open:-": ("ui", "n:admin_users:open"),
    "ui:admin_users:back:-": ("ui", "n:admin_users:back"),
    "ui:admin_payments:open:-": ("ui", "n:admin_payments:open"),
    "ui:admin_payments:filter:succeeded": ("ui", "n:admin_payments:filter.succeeded"),
    "admin_grant_17_basic_1": ("admin_grant", "ad:access:grant:17.basic.1"),
    "friend_grant_3m_900000101": ("friend_grant", "ad:friend:grant_3m:900000101"),
    "admin_promo_grant_1m_900000101": ("admin_promo_grant", "ad:promo_req:grant_1m:900000101"),
    "admin_promo_grant_forever_900000101": ("admin_promo_grant", "ad:promo_req:grant_forever:900000101"),
}

# 2.x strings with no 3.0 target on purpose: the fallback answers them and opens
# the main menu. pay_yookassa_obhod_*: 2.1 already refused to sell packages from
# that button; bare check_payment had no payment id.
BY_DESIGN_FALLBACK = {"pay_yookassa_obhod_250_1", "check_payment"}

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


def _specific(found):
    return [h for h in found if h[0] != FALLBACK]


async def test_every_packed_callback_resolves_to_exactly_one_new_handler(flow):
    problems = []
    for data in PACKED_SAMPLES:
        found = await _accepting(flow.dp, flow.bot, data)
        specific = _specific(found)
        if len(specific) != 1 or not specific[0][0].startswith("r3_"):
            problems.append(f"{data}: expected 1 specific new handler, got {found}")
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
    target = rewrite(legacy) or legacy
    found = await _accepting(flow.dp, flow.bot, target)
    specific = _specific(found)
    if legacy.startswith("sitelogin:"):
        assert [h[0] for h in specific] == [SITE_LOGIN]
        return
    if legacy in BY_DESIGN_FALLBACK:
        assert not specific and [h[0] for h in found] == [FALLBACK]
        return
    assert len(specific) == 1, f"{legacy} -> {target}: expected exactly one handler, got {found}"
    assert specific[0][0].startswith("r3_")


async def test_unknown_callback_lands_on_the_fallback_only(flow):
    found = await _accepting(flow.dp, flow.bot, "totally_unknown_button")
    assert [h[0] for h in found] == [FALLBACK]


def test_packed_samples_cover_every_callback_class():
    prefixes = {k.split(":", 1)[0] for k in PACKED_SAMPLES}
    missing = [c.__name__ for c in cb.ALL_CALLBACKS if c.__prefix__ not in prefixes and c is not cb.Bc]
    assert not missing
