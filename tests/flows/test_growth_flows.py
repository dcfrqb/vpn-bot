"""Stream E through the real dispatcher: deep links, /trial, requests and
grants, broadcast user buttons, admin guards on every admin callback."""
from __future__ import annotations

import pytest

from app.bot import callbacks as cb
from app.bot.middlewares.admin_guard import NO_RIGHTS, AdminGuard
from app.domain.models import AdminTopic
from tests.fakes.bot import user
from tests.growth.fakes import FakeProvisioning, FakeStatus, MemoryLedger, MemoryPromoRepo

ADMIN = 900000199
USER = 900000101


@pytest.fixture
def g(flow, monkeypatch):
    """flow + a container with fake provisioning/status and a PromoEngine on memory."""
    from app.container import build_container, set_container
    from app.services.promo import PromoEngine
    from tests.fakes.remnawave import FakeRemnaGateway

    monkeypatch.setattr("app.config.settings.ADMINS", [ADMIN])
    monkeypatch.setattr("app.config.settings.PROMO_CODES_ENABLED", True)
    monkeypatch.setattr("app.config.settings.GIFTS_ENABLED", True)
    monkeypatch.setattr("app.services.grants.SqlRedemptionLedger", MemoryLedger)

    async def _noop(_tg):
        return None

    monkeypatch.setattr("app.services.grants._ensure_user", _noop)
    # hermetic even when a developer .env points DATABASE_URL at a real server
    monkeypatch.setattr("app.services.admin_stats._session", lambda: None)
    status = FakeStatus()
    prov = FakeProvisioning(status)
    repo = MemoryPromoRepo()
    engine = PromoEngine(provisioning=prov, status=status, notifier=flow.notifier, repo=repo)
    c = build_container(flow.bot, remna=FakeRemnaGateway(), notifier=flow.notifier, provisioning=prov,
                        status=status, promo=engine)
    flow.di.container = c
    set_container(c)
    flow.prov, flow.status, flow.repo, flow.engine = prov, status, repo, engine
    return flow


def _texts(flow) -> list[str]:
    return [c.text for c in flow.session.calls if c.method in ("SendMessage", "EditMessageText") and c.text]


# ----------------------------------------------------------------- deep links and commands


async def test_start_trial_deep_link_redeems(g):
    await g.send("/start trial")
    assert g.prov.effective == 1
    assert any("Пробный период включен" in t for t in _texts(g))


async def test_start_gift_deep_link(g):
    code = await g.engine.create_gift(777, "pro", 1, payment_id=1)
    await g.send(f"/start {code}")
    assert g.prov.calls and g.prov.calls[0][1].plan_code == "pro"
    assert any("Подарок активирован" in t for t in _texts(g))


async def test_login_deep_link_never_reaches_promo(g, monkeypatch):
    from app.routers import site_login

    monkeypatch.setattr("app.config.settings.SITE_INTERNAL_TOKEN", None)
    await g.send("/start login_" + "A" * 20)
    assert [c.text for c in g.session.calls_of("SendMessage")] == [site_login.FEATURE_OFF_TEXT]
    assert not g.prov.calls


async def test_unknown_start_payload_is_not_taken(g):
    from aiogram.filters import CommandObject

    from app.bot.routers.promo_deeplink import is_promo_link

    assert not await is_promo_link(None, CommandObject(command="start", args="ref_abc"), g.container)
    assert not await is_promo_link(None, CommandObject(command="start", args="login_" + "A" * 20), g.container)
    assert await is_promo_link(None, CommandObject(command="start", args="sun718"), g.container)


async def test_trial_command_twice(g):
    await g.send("/trial")
    await g.send("/trial")
    assert g.prov.effective == 1
    texts = _texts(g)
    assert any("Пробный период включен" in t for t in texts)
    assert any("уже был использован" in t for t in texts)


async def test_promo_code_via_input_state(g):
    from app.services.promo import PromoCodeSpec

    await g.engine.create_code(PromoCodeSpec(code="autumn", days=3, plan_code="lite"))
    await g.press(cb.PromoAct(a="enter").pack())
    await g.send("AUTUMN")
    assert g.prov.effective == 1 and g.prov.calls[0][1].days == 3


async def test_promo_code_button_trial(g):
    await g.press(cb.PromoAct(a="trial").pack())
    assert g.prov.effective == 1


# ----------------------------------------------------------------- /friend -> admin grant


def _admin_markup(g):
    sent = g.notifier.to_admins(AdminTopic.GENERAL)
    assert sent, "request not sent to admins"
    return sent[-1].reply_markup


async def test_friend_request_grant_once(g):
    await g.send("/friend")
    rows = _admin_markup(g).inline_keyboard
    grant_1m = rows[0][0].callback_data
    assert cb.Adm.unpack(grant_1m).s == "friend"
    admin = user(ADMIN)
    await g.press(grant_1m, u=admin)
    await g.press(rows[1][0].callback_data, u=admin)  # second admin click: 3m
    assert g.prov.effective == 1 and g.prov.calls[0][1].plan_code == "pro" and g.prov.calls[0][1].days == 30
    alerts = [c.params.get("text") for c in g.answers()]
    assert any("уже обработан" in (t or "") for t in alerts)
    assert [s for s in g.notifier.sent if s.kind == "user" and s.target == USER]


async def test_friend_request_refused_for_active_user(g):
    from datetime import datetime, timedelta, timezone

    g.status.set(USER, active=True, plan_code="lite", expires_at=datetime.now(timezone.utc) + timedelta(days=3))
    await g.send("/friend")
    assert not g.notifier.to_admins(AdminTopic.GENERAL)


async def test_legacy_friend_button_lands_on_new_handler(g):
    await g.press(f"friend_grant_forever_{USER}", u=user(ADMIN))
    assert g.prov.calls and g.prov.calls[0][1].is_lifetime


async def test_admin_command_from_user_sends_promo_request(g):
    await g.send("/admin")
    rows = _admin_markup(g).inline_keyboard
    assert cb.Adm.unpack(rows[0][0].callback_data).s == "promo_req"


async def test_grant_command(g):
    from datetime import datetime, timedelta, timezone

    g.status.set(555, active=True, plan_code="standard", expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    await g.send("/grant 555 7", u=user(ADMIN))
    assert g.prov.calls[0][1].plan_code == "standard" and g.prov.calls[0][1].days == 7
    await g.send("/grant 556 7", u=user(ADMIN))  # no subscription, no plan
    assert any("Продлевать нечего" in t for t in _texts(g))


# ----------------------------------------------------------------- broadcast user buttons


async def test_unsub_and_stop(g, monkeypatch):
    calls = []

    async def fake_opt_out(uid, value):
        calls.append((uid, value))

    monkeypatch.setattr("app.services.broadcast.set_opt_out", fake_opt_out)
    await g.press("bc:unsub")
    await g.send("/stop")
    assert calls == [(USER, True), (USER, True)]
    assert g.session.calls_of("EditMessageReplyMarkup")


# ----------------------------------------------------------------- admin guards


ADMIN_CALLBACKS = [
    cb.Adm(s="panel", a="open"), cb.Adm(s="panel", a="back"), cb.Adm(s="stats", a="open"),
    cb.Adm(s="users", a="open"), cb.Adm(s="users", a="page", arg="2"), cb.Adm(s="payments", a="open"),
    cb.Adm(s="payments", a="filter", arg="succeeded"), cb.Adm(s="payments", a="page", arg="2.all"),
    cb.Adm(s="friend", a="grant_1m", arg=f"{USER}.1"), cb.Adm(s="friend", a="reject", arg=str(USER)),
    cb.Adm(s="promo_req", a="grant_forever", arg=str(USER)), cb.Adm(s="access", a="grant", arg="1.pro.1"),
    cb.Adm(s="obhod", a="open"), cb.Adm(s="obhod", a="off", arg=str(USER)),
    cb.Adm(s="obhod", a="offok", arg=str(USER)), cb.Adm(s="obhod", a="pkg", arg=f"{USER}.obhod_250"),
    cb.Adm(s="block", a="open"), cb.Adm(s="ref", a="open"),
    cb.Nav(s="admin_panel", p="open"), cb.Nav(s="admin_users", p="page.2"), cb.Nav(s="admin_payments", p="page.2&all"),
    cb.PromoAdm(a="list"), cb.PromoAdm(a="show", id=1), cb.PromoAdm(a="on", id=1), cb.PromoAdm(a="off", id=1),
    cb.BcAdm(a="new"), cb.BcAdm(a="abort"), cb.BcAdm(a="list"), cb.BcAdm(a="show", id=1), cb.BcAdm(a="prev", id=1),
    cb.BcAdm(a="go", id=1), cb.BcAdm(a="go2", id=1), cb.BcAdm(a="stats", id=1), cb.BcAdm(a="cancel", id=1),
    cb.BcAdm(a="del", id=1),
]
LEGACY_ADMIN_STRINGS = ["admin_panel", "admin_back", "admin_stats", "admin_users", "admin_payments",
                        "admin_users_page_2", "admin_payments_all", f"friend_grant_1m_{USER}",
                        f"admin_promo_grant_3m_{USER}", f"admin_promo_reject_{USER}", "admin_grant_forever_17"]


@pytest.mark.parametrize("data", [c.pack() for c in ADMIN_CALLBACKS] + LEGACY_ADMIN_STRINGS)
async def test_non_admin_gets_no_rights_on_every_admin_callback(g, data):
    await g.press(data)
    answers = g.answers()
    assert len(answers) == 1 and answers[0].params.get("text") == NO_RIGHTS
    assert answers[0].params.get("show_alert") is True
    assert not g.session.calls_of("SendMessage") and not g.session.calls_of("EditMessageText")
    assert not g.prov.calls and not g.notifier.sent


@pytest.mark.parametrize("cmd", ["/stats", "/whois 1", "/sync 1", "/syncme", "/block 5", "/unblock 5",
                                 "/stoplist", "/stoplist_add 5", "/stoplist_del 5", "/referral_stats",
                                 "/referral_payout sun718 1", "/payments_new", "/payment_find X", "/legacy_hits",
                                 "/grant 5 5", "/promo_new x days=1", "/promo_list", "/obhod 5", "/bc_new",
                                 "/bc_list", "/bc_preview 1", "/bc_send 1", "/bc_send_to 1 2", "/bc_stats 1",
                                 "/bc_cancel 1"])
async def test_non_admin_commands_are_ignored(g, cmd):
    await g.send(cmd)
    assert not g.session.calls and not g.prov.calls and not g.notifier.sent


def test_every_admin_handler_sits_behind_admin_guard(g):
    """Structural: each handler of every stream E admin router has AdminGuard in its chain."""
    names = {"r3_admin_home", "r3_admin_users", "r3_admin_grants", "r3_admin_ops", "r3_admin_promo",
             "r3_broadcast_admin", "r3_admin_obhod"}
    seen = set()
    for router in g.dp.chain_tail:
        if router.name not in names:
            continue
        seen.add(router.name)
        for observer in (router.message, router.callback_query):
            if not observer.handlers:
                continue
            guards = [m for r in router.chain_head for m in r.observers[observer.event_name].middleware._middlewares
                      if isinstance(m, AdminGuard)]
            assert guards, f"{router.name}.{observer.event_name} has no AdminGuard"
    assert seen == names


async def test_admin_panel_opens_for_admin(g):
    await g.press(cb.Adm(s="panel", a="open").pack(), u=user(ADMIN))
    assert any("Админ-панель" in t for t in _texts(g))
    await g.send("/admin", u=user(ADMIN))
    assert sum("Админ-панель" in t for t in _texts(g)) == 2


# ----------------------------------------------------------------- review UX M7 / m12


async def test_commands_are_not_taken_as_a_promo_code(g):
    await g.send("/promo")
    await g.send("/start")
    texts = _texts(g)
    assert not any("/start" in t and "не найден" in t for t in texts)
    assert "Профиль" in texts[-1]
    await g.send("spring")  # the wait ended with /start: plain text is not a code now
    assert not any("spring" in t for t in _texts(g))


async def test_trial_command_answers_when_trial_is_off(g, monkeypatch):
    from app.domain.texts.connect import TRIAL_UNAVAILABLE

    monkeypatch.setattr("app.config.settings.PROMO_TRIAL_ENABLED", False)
    await g.send("/trial")
    assert _texts(g)[-1] == TRIAL_UNAVAILABLE
    assert not g.prov.calls


def test_command_menu_follows_the_flags():
    from types import SimpleNamespace

    from app.bot.routers.start import commands_for

    names = [c.command for c in commands_for(SimpleNamespace(PROMO_TRIAL_ENABLED=False, PROMO_CODES_ENABLED=False))]
    assert "trial" not in names and "promo" not in names and "start" in names
    names = [c.command for c in commands_for(SimpleNamespace(PROMO_TRIAL_ENABLED=True, PROMO_CODES_ENABLED=True))]
    assert "trial" in names and "promo" in names
