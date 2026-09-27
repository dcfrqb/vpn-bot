"""Admin screens (stream E): pure functions (DTO -> View), screen kit (release 3.0).

Text/layout comes from ``app.domain.texts.admin`` (``*_screen`` -> ``ui.Screen``);
this module only lays out buttons with ``app.bot.views.kit``.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from app.bot.callbacks import Adm, BcAdm, PromoAdm
from app.bot.views import kit
from app.bot.views import url_btn
from app.domain.texts import admin as T
from app.domain.texts.ui import B

View = kit.View

BACK_TO_PANEL = kit.Footer.to_admin()


def home(stats: Any) -> View:
    return kit.view(
        T.panel_screen(stats),
        options=[
            kit.pair(kit.action(T.BTN_STATS, Adm(s="stats", a="open")), kit.action(T.BTN_USERS, Adm(s="users", a="open"))),
            kit.pair(kit.action(T.BTN_PAYMENTS, Adm(s="payments", a="open")), kit.action(T.BTN_PROMO, PromoAdm(a="list"))),
            kit.pair(kit.action(T.BTN_BROADCASTS, BcAdm(a="list")), kit.action(T.BTN_OBHOD, Adm(s="obhod", a="open"))),
            kit.pair(kit.action(T.BTN_BLOCKLIST, Adm(s="block", a="open")), kit.action(T.BTN_REFERRAL, Adm(s="ref", a="open"))),
            kit.action(T.BTN_MAINT, Adm(s="maint", a="show")),  # stream C: bot/routers/admin/panel.py
        ],
        footer=kit.Footer.to_menu(),
    )


def stats(s: Any) -> View:
    return kit.view(
        T.stats_screen(s),
        primary=[kit.action(T.BTN_REFRESH, Adm(s="stats", a="open"))],
        footer=BACK_TO_PANEL,
    )


def _pager(section: str, page: int, total_pages: int, suffix: str = "") -> Optional[tuple]:
    left = kit.action(B.PREV, Adm(s=section, a="page", arg=f"{page - 1}{suffix}")) if page > 1 else None
    right = kit.action(B.NEXT, Adm(s=section, a="page", arg=f"{page + 1}{suffix}")) if page < total_pages else None
    return kit.pair(left, right) if (left or right) else None


def users(data: dict) -> View:
    return kit.view(
        T.users_screen(data),
        secondary=[_pager("users", int(data.get("page", 1)), int(data.get("total_pages", 1)))],
        footer=BACK_TO_PANEL,
    )


PAYMENT_FILTERS = T.PAYMENT_FILTERS


def payments(data: dict, flt: str) -> View:
    flt = flt if flt in PAYMENT_FILTERS else "all"
    return kit.view(
        T.payments_screen(data, flt),
        options=[[kit.action("📊 Все", Adm(s="payments", a="filter", arg="all")),
                 kit.action("✅", Adm(s="payments", a="filter", arg="succeeded")),
                 kit.action("⏳", Adm(s="payments", a="filter", arg="pending"))]],
        secondary=[_pager("payments", int(data.get("page", 1)), int(data.get("total_pages", 1)), f".{flt}")],
        footer=BACK_TO_PANEL,
    )


def whois(card: Any, state: Any, *, bot_blocked: bool, is_admin: bool) -> View:
    return kit.view(T.whois_screen(card, state, bot_blocked=bot_blocked, is_admin=is_admin), footer=BACK_TO_PANEL)


def request_keyboard(section: str, arg: str, user_id: int):
    """/friend (section friend) and /admin (section promo_req) request buttons.

    Left hand-built (out of scope): the request text itself lives in
    ``app.bot.routers.trial_promo`` (access requests), not in the admin alert
    categories this migration covers.
    """
    return kit.keyboard(
        primary=[
            kit.action(T.BTN_GRANT_1M, Adm(s=section, a="grant_1m", arg=arg)),
            kit.action(T.BTN_GRANT_3M, Adm(s=section, a="grant_3m", arg=arg)),
            kit.action(T.BTN_GRANT_FOREVER, Adm(s=section, a="grant_forever", arg=arg)),
            kit.action(T.BTN_REJECT, Adm(s=section, a="reject", arg=arg)),
        ],
        links=[url_btn("📩 Написать пользователю", f"tg://user?id={int(user_id)}")],
    )


def referral(st: Any) -> View:
    return kit.view(T.referral_screen(st), footer=BACK_TO_PANEL)


def obhod_overview(ov: dict) -> View:
    return kit.view(T.obhod_overview_screen(ov), footer=BACK_TO_PANEL)


def obhod_card(info: Any, packages: Sequence[tuple[str, str]]) -> View:
    tg = info.telegram_id
    if not info.exists:
        screen = T.result_screen("info", f"Обход {tg}", f"У <code>{tg}</code> нет подписки обхода (она выдается с Pro).")
        return kit.view(screen, footer=BACK_TO_PANEL)
    options = [kit.action(f"➕ {title}", Adm(s="obhod", a="pkg", arg=f"{tg}.{code}")) for code, title in packages]
    return kit.view(
        T.obhod_card_screen(info),
        options=options,
        secondary=[
            kit.pair(kit.action("↩️ Базовый лимит", Adm(s="obhod", a="base", arg=str(tg))),
                     kit.action("⛔ Выключить", Adm(s="obhod", a="off", arg=str(tg)))),
            kit.action(T.BTN_REFRESH, Adm(s="obhod", a="show", arg=str(tg))),
        ],
        footer=BACK_TO_PANEL,
    )


def note(screen: Any) -> View:
    """An admin result/notice (command reply, error) with the way back: [👑 В админку]."""
    return kit.view(screen, footer=BACK_TO_PANEL)


def plain(text: str) -> View:
    """A hand-written admin text (usage help, 2.x logs) with [👑 В админку]."""
    return kit.View(text, kit.keyboard(footer=BACK_TO_PANEL))


def confirm(question: str, yes: Any, no: Any) -> View:
    return kit.view(
        T.confirm_screen(question),
        primary=[kit.pair(kit.action("✅ Да", yes), kit.action(B.CANCEL, no))],
    )


def blocklist(users: list, cards: list) -> View:
    return kit.view(T.blocklist_screen(users, cards), footer=BACK_TO_PANEL)


# ----------------------------------------------------------------- promo codes


def promo_list(rows: list) -> View:
    buttons = [kit.action(f"{'🟢' if r.is_active else '⚪️'} {r.code}", PromoAdm(a="show", id=r.id)) for r in rows[:15]]
    return kit.view(T.promo_list_screen(rows), options=buttons, footer=BACK_TO_PANEL)


def promo_card(r: Any, bot_username: Optional[str] = None) -> View:
    toggle = (kit.action("⚪️ Выключить", PromoAdm(a="off", id=r.id)) if r.is_active
              else kit.action("🟢 Включить", PromoAdm(a="on", id=r.id)))
    return kit.view(T.promo_card_screen(r, bot_username), primary=[toggle],
                     footer=kit.Footer.back_admin(PromoAdm(a="list"), label=B.TO_LIST))


__all__ = [
    "BACK_TO_PANEL", "PAYMENT_FILTERS", "View", "blocklist", "confirm", "home", "note", "plain", "obhod_card", "obhod_overview",
    "payments", "promo_card", "promo_list", "referral", "request_keyboard", "stats", "users", "whois",
]
