"""Stream C: Remnawave webhook events -> actions (app.worker.panel_events)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.domain.models import AdminTopic
from app.services.events_repo import GRACE_ACTIVE, ReminderInfo
from app.worker.panel_events import PanelEventProcessor, parse_event
from tests.events.conftest import NOW
from tests.events.payloads import event, hwid_event, node, user
from tests.fakes.bot import markup_rows


def proc(env):
    return PanelEventProcessor(env.c, env.repo, settings=env.settings, clock=lambda: NOW)


def buttons(sent):
    return [b["data"] for row in markup_rows(sent.reply_markup) or [] for b in row if b["data"]]


def plain(text):
    return text.replace("\xa0", " ")


def user_sent(env):
    return [s for s in env.notifier.sent if s.kind == "user"]


# ------------------------------------------------------------------ parsing


def test_parse_event_shapes_and_rejects_garbage():
    ev = parse_event(event("user.expired", user(), ts=NOW))
    assert ev.scope == "user" and ev.user.id == 1500 and ev.user.telegram_id == 700001
    assert ev.user.squads == ("pro",) and ev.timestamp == NOW
    hw = parse_event(hwid_event(user(), ts=NOW))
    assert hw.user.id == 1500 and hw.device["deviceModel"] == "iPhone 15"
    nd = parse_event(event("node.connection_lost", node(), ts=NOW))
    assert nd.user is None and nd.node["name"] == "nl-1"
    for bad in ([], {"scope": "user"}, {"scope": "user", "event": "node.created"}, {"scope": 1, "event": "x"}):
        with pytest.raises(ValueError):
            parse_event(bad)


def test_event_repr_never_contains_secrets():
    ev = parse_event(event("user.expired", user(), ts=NOW))
    text = repr(ev) + repr(ev.user)
    assert "SECRET" not in text and "sub.example" not in text


# ------------------------------------------------------------------ user.expired


async def test_expired_marks_row_disables_obhod_and_invalidates(env):
    env.repo.add(700001, panel_id=1500, valid_until=NOW - timedelta(minutes=1), has_paid=True)
    env.repo.obhod[700001] = {"panel_id": "1600", "active": True}
    env.panel.add_user(1600, "tg_700001_obhod", squads=["obhod"])
    out = await proc(env).process(parse_event(event("user.expired", user(status="EXPIRED"), ts=NOW)))
    assert out == "expired_marked"
    assert env.repo.subs[700001]["active"] is False
    assert env.panel.disabled == [1600] and env.repo.obhod[700001]["active"] is False
    assert env.c.status.invalidated == [700001]
    assert not user_sent(env)  # grace is off: the +1d reminder talks to the user


async def test_expired_does_nothing_when_db_term_is_later(env):
    env.repo.add(700001, valid_until=NOW + timedelta(days=30))
    env.repo.obhod[700001] = {"panel_id": "1600", "active": True}
    out = await proc(env).process(parse_event(event("user.expired", user(status="EXPIRED"), ts=NOW)))
    assert out == "paid_later"
    assert env.repo.subs[700001]["active"] is True and env.repo.obhod[700001]["active"] is True
    assert env.panel.disabled == []


async def test_expired_of_obhod_account_is_not_treated_as_main(env):
    env.repo.obhod[700001] = {"panel_id": "1600", "active": True}
    u = user(uid=1600, tg=None, username="tg_700001_obhod", squads=(("obhod", "sq-obhod"),))
    out = await proc(env).process(parse_event(event("user.expired", u, ts=NOW)))
    assert out == "not_main" and ("mark_expired", 700001) not in env.repo.calls
    assert env.c.status.invalidated == [700001]


async def test_expired_starts_grace_when_enabled(env):
    env.settings.GRACE_ENABLED = True
    env.panel.add_user(1500, "tg_700001", telegram_id=700001, squads=["pro"], limit=10,
                       expire="2026-09-23T08:00:00Z", status="EXPIRED")
    env.repo.add(700001, panel_id=1500, valid_until=NOW - timedelta(hours=1), has_paid=True,
                 last_plan_code="pro", last_months=3)
    out = await proc(env).process(parse_event(event("user.expired", user(status="EXPIRED"), ts=NOW)))
    assert out == "grace_started"
    assert env.repo.subs[700001]["grace_state"] == GRACE_ACTIVE
    assert env.panel.squad_names(1500) == ["grace"]
    msg = user_sent(env)[0]
    assert msg.target == 700001 and "3 дня" in plain(msg.text) and "5 ГБ" in plain(msg.text)
    assert buttons(msg) == ["pe:pro:3"]
    assert env.notifier.to_admins(AdminTopic.PANEL)


# ------------------------------------------------------------------ not_connected


async def test_not_connected_nudges_once_with_connect_button(env):
    body = event("user.not_connected", user(), ts=NOW, meta={"notConnectedAfterHours": 24})
    p = proc(env)
    assert await p.process(parse_event(body)) == "nudged"
    assert await p.process(parse_event(body)) == "deduped"
    msg = user_sent(env)[0]
    assert buttons(msg) == ["n:connect:"]
    rows = markup_rows(msg.reply_markup)
    assert rows[1][0]["url"] == "https://telegra.ph/crs"


async def test_not_connected_skips_expired_and_obhod(env):
    p = proc(env)
    assert await p.process(parse_event(event("user.not_connected", user(status="EXPIRED"), ts=NOW,
                                             meta={"notConnectedAfterHours": 24}))) == "not_active"
    ob = user(uid=1600, tg=None, username="tg_700001_obhod")
    assert await p.process(parse_event(event("user.not_connected", ob, ts=NOW, meta=None))) == "skipped"
    assert not user_sent(env)


# ------------------------------------------------------------------ hwid added


async def test_device_added_counts_devices_and_offers_devices_screen(env):
    env.panel.devices[1500] = [{"hwid": "A"}, {"hwid": "B"}, {"hwid": "HWID-SECRET-123456"}]
    p = proc(env)
    body = hwid_event(user(limit=5), ts=NOW)
    assert await p.process(parse_event(body)) == "notified"
    assert await p.process(parse_event(body)) == "deduped"  # panel retry
    msg = user_sent(env)[0]
    assert "iPhone 15" in msg.text and "3 из 5" in msg.text and "@dcfrq" in msg.text
    assert buttons(msg) == ["dv:list:"]
    assert "HWID-SECRET" not in (msg.dedup_key or "")
    assert env.c.status.invalidated == [700001, 700001]


async def test_device_added_without_device_api_still_notifies(env):
    class NoDevices:
        async def list_devices(self, panel_id):
            raise NotImplementedError

        async def get_user(self, panel_id):
            return None

    env.c.remna = NoDevices()
    assert await proc(env).process(parse_event(hwid_event(user(limit=5), ts=NOW))) == "notified"
    text = plain(user_sent(env)[0].text)
    assert "Занято" not in text and "5 устройств" in text


# ------------------------------------------------------------------ modified / other user events


async def test_user_modified_invalidates_status_of_owner(env):
    env.repo.obhod[700001] = {"panel_id": "1600", "active": True}
    p = proc(env)
    await p.process(parse_event(event("user.modified", user(tg=700002), ts=NOW)))
    await p.process(parse_event(event("user.modified", user(uid=1600, tg=None, username="x"), ts=NOW)))
    await p.process(parse_event(event("user.enabled", user(tg=700003), ts=NOW)))
    assert env.c.status.invalidated == [700002, 700001, 700003]
    assert not env.notifier.sent


# ------------------------------------------------------------------ nodes


async def test_node_lost_and_restored_go_to_admin_panel_topic_deduped(env):
    p = proc(env)
    assert await p.process(parse_event(event("node.connection_lost", node(), ts=NOW))) == "notified"
    assert await p.process(parse_event(event("node.connection_lost", node(), ts=NOW))) == "deduped"
    assert await p.process(parse_event(event("node.connection_restored", node(message=None), ts=NOW))) == "notified"
    texts = [s.text for s in env.notifier.to_admins(AdminTopic.PANEL)]
    assert "nl-1" in texts[0] and "недоступна" in texts[0] and "timeout" in texts[0]
    assert "снова на связи" in texts[1]
    assert not user_sent(env)


async def test_other_scopes_are_ignored(env):
    body = {"scope": "service", "event": "service.login_attempt_failed", "timestamp": "2026-09-23T09:00:00.000Z",
            "data": {"loginAttempt": {"username": "x", "ip": "1.1.1.1", "userAgent": "y"}}}
    assert await proc(env).process(parse_event(body)) == "ignored"
    assert not env.notifier.sent


# ------------------------------------------------------------------ obhod limited


async def test_obhod_limited_upsells_packages_once_a_month(env):
    env.repo.obhod[700001] = {"panel_id": "1600", "active": True}
    u = user(uid=1600, tg=None, username="tg_700001_obhod", status="LIMITED", squads=(("obhod", "sq-obhod"),),
             traffic_limit=100 * 1024 ** 3, strategy="MONTH")
    p = proc(env)
    assert await p.process(parse_event(event("user.limited", u, ts=NOW))) == "upsell"
    assert await p.process(parse_event(event("user.limited", u, ts=NOW))) == "deduped"
    msg = user_sent(env)[0]
    assert msg.target == 700001 and "100 ГБ" in plain(msg.text) and "докупить" in msg.text
    assert buttons(msg) == ["n:plans:obhod"]  # the 3.0 packages screen (review UX m24)


async def test_obhod_owner_found_by_username_when_row_is_missing(env):
    u = user(uid=1601, tg=None, username="tg_700009_obhod", status="LIMITED", squads=(("obhod", "sq-obhod"),))
    assert await proc(env).process(parse_event(event("user.limited", u, ts=NOW))) == "upsell"
    assert user_sent(env)[0].target == 700009


async def test_limited_main_user_is_not_upsold(env):
    u = user(status="LIMITED")
    assert await proc(env).process(parse_event(event("user.limited", u, ts=NOW))) == "not_obhod"
    assert not user_sent(env)


async def test_renew_button_falls_back_to_plans_when_not_sellable(env):
    from app.worker.panel_events import renew_target

    assert await renew_target(env.c, 1, ReminderInfo(1, last_plan_code="basic", last_months=1)) == (None, None)
    assert await renew_target(env.c, 1, ReminderInfo(1, last_plan_code="pro", last_months=None)) == ("pro", 1)
    assert await renew_target(env.c, 1, ReminderInfo(1)) == (None, None)


def test_processor_default_repo_is_sql():
    from app.services.events_repo import SqlEventsRepo

    class C:
        settings = None

    assert isinstance(PanelEventProcessor(C()).repo, SqlEventsRepo)
