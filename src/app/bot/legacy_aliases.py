"""Legacy callback aliases (release 3.0 Foundation). Keep >= 3 months after 3.0.

Old messages in users' chats keep 2.x buttons (``back_to_main``,
``pay_yookassa_pro_1``, ``ui:main_menu:open:-``...). This outer callback
middleware maps each such string to a packed 3.0 callback:

  1. find the alias (ordered table below; first match wins);
  2. INCR ``legacy_hits:<alias key>`` in Redis (fail-open) so we can see when
     an alias is dead and can be dropped;
  3. build the packed callback; if some handler of the 3.0 routers
     (app.bot.routers) accepts the rewritten event, the event continues
     with the new ``data``; otherwise the ORIGINAL event continues (after
     the 3.0 cutover only the site_login router and the r3_fallback
     catch-all are left for it).

tests/flows/test_callback_matrix.py proves every 2.1.1 string reaches exactly
one specific 3.0 handler. Money never comes from the old string:
``pay_yookassa_<plan>_<months>_<amount>`` maps to Period(plan, months).

The rewritten event is a ``model_copy`` of the original CallbackQuery: it
stays bound to the same Bot, so ``callback.answer()`` / ``message.edit_text``
work (tests/test_r3_legacy_aliases.py proves it).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Optional

from aiogram import BaseMiddleware, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery

from app.bot.callbacks import Adm, AdmReview, Bc, Nav, PayCheck, Period, Plan
from app.logger import logger

HIT_KEY_PREFIX = "legacy_hits:"

Builder = Callable[[re.Match], Optional[CallbackData]]


@dataclass(frozen=True)
class Alias:
    key: str  # counter key and documentation name
    pattern: re.Pattern
    build: Optional[Builder]  # None = passthrough only
    count: bool = True

    def match(self, data: str) -> Optional[re.Match]:
        return self.pattern.fullmatch(data)


def _a(key: str, regex: str, build: Optional[Builder], count: bool = True) -> Alias:
    return Alias(key=key, pattern=re.compile(regex), build=build, count=count)


def _nav(s: str, p: str = "") -> Builder:
    return lambda m: Nav(s=s, p=p)


def _adm(s: str, a: str, group: Optional[int] = None) -> Builder:
    return lambda m: Adm(s=s, a=a, arg=(m.group(group) if group else ""))


def _ui(m: re.Match) -> Optional[CallbackData]:
    screen, action, payload = m.group(1), m.group(2), m.group(3) or "-"
    p = action if payload in ("", "-") else f"{action}.{payload}"
    try:
        cb = Nav(s=screen, p=p)
        cb.pack()
    except (ValueError, TypeError):
        return None
    return cb


def _pay(m: re.Match) -> Optional[CallbackData]:
    """pay_yookassa_{plan}[_{months}[_{amount}]]; the amount is ignored."""
    plan = m.group(1).lower()
    months = int(m.group(2)) if m.group(2) else 1
    if months <= 0:
        return None
    return Period(c=plan, m=months)


def _payments_page(m: re.Match) -> CallbackData:
    page, flt = m.group(1), (m.group(2) or "all").lstrip("_") or "all"
    return Adm(s="payments", a="page", arg=f"{page}.{flt}")


# Ordered: specific before generic. Every 2.x callback string the bot ever
# produced must match exactly one alias (tests/test_r3_callback_matrix.py).
ALIASES: tuple[Alias, ...] = (
    # site login is owned by the site: never rewritten, never counted
    _a("sitelogin", r"sitelogin:.*", None, count=False),
    # user navigation
    _a("back_to_main", r"back_to_main", _nav("main")),
    _a("buy_subscription", r"buy_subscription", _nav("plans")),
    _a("connect_vpn", r"connect_vpn", _nav("connect")),
    _a("get_subscription_link", r"get_subscription_link", _nav("connect", "link")),
    _a("help", r"help", _nav("help")),
    _a("refresh_info", r"refresh_info", _nav("main", "refresh")),
    _a("my_plan", r"my_plan", _nav("plan")),
    _a("friend_request", r"friend_request_(yes|no)", lambda m: Nav(s="friend_req", p=m.group(1))),
    # plans and payment
    _a("plan_period", r"plan_([a-z]+)_(\d{1,2})", lambda m: Period(c=m.group(1), m=int(m.group(2)))),
    _a("plan", r"plan_([a-z]+)", lambda m: Plan(c=m.group(1))),
    _a("pay_yookassa", r"pay_yookassa_([a-z0-9]+)(?:_(\d{1,2}))?(?:_\d+(?:\.\d+)?)?", _pay),
    _a("pay_yookassa_other", r"pay_yookassa_.*", None),
    _a("check_payment", r"check_payment:([A-Za-z0-9-]{1,48})", lambda m: PayCheck(pid=0, ext=m.group(1))),
    _a("check_payment_bare", r"check_payment", None),
    # 2.x screen-manager callbacks
    _a("ui", r"ui:([a-z0-9_]+):([a-z0-9_]+)(?::([^:]*))?", _ui),
    # admin: requests and grants
    _a("admin_promo_grant", r"admin_promo_grant_(1m|3m|forever)_(\d+)",
       lambda m: Adm(s="promo_req", a=f"grant_{m.group(1)}", arg=m.group(2))),
    _a("admin_promo_reject", r"admin_promo_reject_(\d+)", _adm("promo_req", "reject", 1)),
    _a("admin_grant_forever", r"admin_grant_forever_(\d+)", _adm("access", "forever", 1)),
    _a("admin_grant", r"admin_grant_(\d+)_([a-z]+)_(\d{1,2})",
       lambda m: Adm(s="access", a="grant", arg=f"{m.group(1)}.{m.group(2)}.{m.group(3)}")),
    _a("admin_reject", r"admin_reject_(\d+)", _adm("access", "reject", 1)),
    _a("friend_grant", r"friend_grant_(1m|3m|forever)_(\d+)",
       lambda m: Adm(s="friend", a=f"grant_{m.group(1)}", arg=m.group(2))),
    _a("friend_reject", r"friend_reject_(\d+)", _adm("friend", "reject", 1)),
    # admin: screens
    _a("admin_users_page", r"admin_users_page_(\d+)", _adm("users", "page", 1)),
    _a("admin_payments_page", r"admin_payments_page_(\d+)(_?[a-z]*)", _payments_page),
    _a("admin_payments_filter", r"admin_payments_([a-z]+)", _adm("payments", "filter", 1)),
    _a("admin_panel", r"admin_panel", _adm("panel", "open")),
    _a("admin_stats", r"admin_stats", _adm("stats", "open")),
    _a("admin_users", r"admin_users", _adm("users", "open")),
    _a("admin_payments", r"admin_payments", _adm("payments", "open")),
    _a("admin_back", r"admin_back", _adm("panel", "back")),
    # held-payment review
    _a("review", r"rv_(ok|no):(\d+)", lambda m: AdmReview(a=m.group(1), pid=int(m.group(2)))),
)

# 2.x broadcast buttons are already valid packed Bc callbacks (bc:unsub,
# bc:close): no alias needed, listed for the matrix test.
NATIVE_LEGACY_STRINGS = (Bc(a="unsub").pack(), Bc(a="close").pack())


def find_alias(data: Optional[str]) -> Optional[tuple[Alias, re.Match]]:
    if not data:
        return None
    for alias in ALIASES:
        m = alias.match(data)
        if m is not None:
            return alias, m
    return None


def rewrite(data: str) -> Optional[str]:
    """Packed 3.0 callback string for a legacy string, or None (passthrough)."""
    found = find_alias(data)
    if not found:
        return None
    alias, m = found
    if alias.build is None:
        return None
    try:
        cb = alias.build(m)
        return cb.pack() if cb is not None else None
    except (ValueError, TypeError) as e:
        logger.debug(f"legacy alias {alias.key}: cannot build ({type(e).__name__})")
        return None


async def count_hit(key: str) -> None:
    from app.infra.redis.flags import incr_counter

    await incr_counter(f"{HIT_KEY_PREFIX}{key}")


async def alias_hit_counts() -> dict[str, int]:
    """{alias key: hits} for an admin screen. Missing/unavailable -> 0."""
    from app.infra.redis.flags import get_value

    out: dict[str, int] = {}
    for alias in ALIASES:
        if not alias.count:
            continue
        raw = await get_value(f"{HIT_KEY_PREFIX}{alias.key}")
        try:
            out[alias.key] = int(raw) if raw is not None else 0
        except ValueError:
            out[alias.key] = 0
    return out


def _walk(routers: Iterable[Router]) -> Iterable[Router]:
    for r in routers:
        yield from r.chain_tail


async def new_handler_accepts(routers: Iterable[Router], event: CallbackQuery, data: dict[str, Any]) -> bool:
    """True if some callback_query handler in ``routers`` (and their children)
    would accept ``event``: router-level filters and handler filters pass."""
    for router in _walk(routers):
        observer = router.callback_query
        ok, _ = await observer.check_root_filters(event, **data)
        if not ok:
            continue
        for handler in observer.handlers:
            accepted, _ = await handler.check(event, **data)
            if accepted:
                return True
    return False


class LegacyAliasMiddleware(BaseMiddleware):
    """Outer middleware on dp.callback_query. See module docstring."""

    def __init__(self, new_routers: Callable[[], Iterable[Router]]):
        self._new_routers = new_routers

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)
        found = find_alias(event.data)
        if not found:
            return await handler(event, data)
        alias, _ = found
        if alias.count:
            await count_hit(alias.key)
        new_data = rewrite(event.data)
        if new_data is None:
            return await handler(event, data)
        new_event = event.model_copy(update={"data": new_data})
        try:
            accepted = await new_handler_accepts(self._new_routers(), new_event, data)
        except Exception as e:  # noqa: BLE001 - a filter bug must not break old buttons
            logger.warning(f"legacy alias {alias.key}: filter check failed ({type(e).__name__}), passthrough")
            accepted = False
        if not accepted:
            return await handler(event, data)
        data["legacy_alias"] = alias.key
        return await handler(new_event, data)
