"""Stream C: POST /webhook/remnawave (signature, window, dedupe, background)."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.routes import remnawave as route
from tests.events.conftest import NOW
from tests.events.payloads import SECRET, encode, event, iso, node, signed_headers, user


@pytest.fixture
def api(env, monkeypatch):
    from app.api.main import app
    from app.container import set_container
    from app.worker.panel_events import PanelEventProcessor

    monkeypatch.setattr("app.config.settings.PANEL_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(route, "clock", lambda: NOW)
    processed = []

    class Recording(PanelEventProcessor):
        async def process(self, ev):
            processed.append(ev.event)
            return await super().process(ev)

    monkeypatch.setattr(route, "processor_factory",
                        lambda c: Recording(c, env.repo, settings=env.settings, clock=lambda: NOW))
    set_container(env.c)
    client = TestClient(app)
    client.processed = processed
    yield client
    set_container(None)


def post(client, body: dict, *, secret: str = SECRET, headers=None, raw: bytes = None):
    raw = raw if raw is not None else encode(body)
    h = signed_headers(raw, secret, ts=body.get("timestamp") if isinstance(body, dict) else None)
    h.update(headers or {})
    return client.post("/webhook/remnawave", content=raw, headers=h)


def test_disabled_without_secret(env, monkeypatch):
    from app.api.main import app

    monkeypatch.setattr("app.config.settings.PANEL_WEBHOOK_SECRET", "")
    r = TestClient(app).post("/webhook/remnawave", content=b"{}", headers={"X-Remnawave-Signature": "x"})
    assert r.status_code == 503 and r.json() == {"status": "disabled"}


def test_valid_event_is_accepted_and_processed_in_background(api, env):
    r = post(api, event("node.connection_lost", node(), ts=NOW))
    assert r.status_code == 200 and r.json() == {"status": "accepted"}
    assert api.processed == ["node.connection_lost"]
    assert env.notifier.to_admins()
    # marker is "done" after processing
    done = [k for k in env.redis.store if k.startswith("rw_webhook:")]
    assert len(done) == 1 and env.redis.store[done[0]].startswith("done:")


@pytest.mark.parametrize("mutate", ["no_header", "wrong_secret", "tampered", "uppercase_ok"])
def test_signature(api, mutate):
    body = event("node.connection_lost", node(), ts=NOW)
    raw = encode(body)
    if mutate == "no_header":
        r = api.post("/webhook/remnawave", content=raw, headers={"Content-Type": "application/json"})
    elif mutate == "wrong_secret":
        r = post(api, body, secret="X" * 40)
    elif mutate == "tampered":
        h = signed_headers(raw)
        r = api.post("/webhook/remnawave", content=raw.replace(b"nl-1", b"nl-9"), headers=h)
    else:
        h = signed_headers(raw)
        h["X-Remnawave-Signature"] = h["X-Remnawave-Signature"].upper()
        r = api.post("/webhook/remnawave", content=raw, headers=h)
    if mutate == "uppercase_ok":
        assert r.status_code == 200
    else:
        assert r.status_code == 401 and api.processed == []


def test_signature_is_over_raw_bytes_not_reserialized_json(api):
    body = event("node.connection_lost", node(), ts=NOW)
    raw = json.dumps(body, indent=2).encode()  # different bytes, same JSON
    r = api.post("/webhook/remnawave", content=raw, headers=signed_headers(encode(body)))
    assert r.status_code == 401
    assert post(api, body, raw=raw).status_code == 200


def test_bad_json_is_400(api):
    raw = b"not json"
    r = api.post("/webhook/remnawave", content=raw, headers=signed_headers(raw))
    assert r.status_code == 400
    raw = encode({"scope": "user", "event": "node.created", "timestamp": iso(NOW), "data": {}})
    assert api.post("/webhook/remnawave", content=raw, headers=signed_headers(raw)).status_code == 400


@pytest.mark.parametrize("shift,ok", [(timedelta(minutes=-29), True), (timedelta(minutes=-31), False),
                                      (timedelta(minutes=4), True), (timedelta(minutes=6), False)])
def test_replay_window_on_signed_body_timestamp(api, shift, ok):
    r = post(api, event("node.connection_lost", node(), ts=NOW + shift))
    assert r.status_code == 200
    assert r.json()["status"] == ("accepted" if ok else "stale")
    assert bool(api.processed) is ok


def test_missing_timestamp_is_stale(api):
    body = event("node.connection_lost", node(), ts=NOW)
    body.pop("timestamp")
    assert post(api, body).json() == {"status": "stale"}


def test_duplicate_delivery_is_processed_once(api, env):
    body = event("user.not_connected", user(), ts=NOW, meta={"notConnectedAfterHours": 24})
    assert post(api, body).json() == {"status": "accepted"}
    assert post(api, body).json() == {"status": "duplicate"}
    assert api.processed == ["user.not_connected"]
    assert len([s for s in env.notifier.sent if s.kind == "user"]) == 1


def test_redis_down_still_processes(api, env):
    env.redis.down = True
    assert post(api, event("node.connection_lost", node(), ts=NOW)).json() == {"status": "accepted"}
    assert api.processed == ["node.connection_lost"]


def test_processing_error_is_reported_to_admins_not_to_panel(api, env, monkeypatch):
    from app.domain.models import AdminTopic

    async def boom(self, ev):
        raise RuntimeError("secret internals")

    monkeypatch.setattr("app.worker.panel_events.PanelEventProcessor.on_node", boom)
    r = post(api, event("node.connection_lost", node(), ts=NOW))
    assert r.status_code == 200
    errs = env.notifier.to_admins(AdminTopic.ERRORS)
    assert errs and "RuntimeError" in errs[0].text and "secret internals" not in errs[0].text


def test_body_is_never_logged(api):
    from app.logger import logger

    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG")
    try:
        post(api, event("user.modified", user(), ts=NOW))
        post(api, event("user.modified", user(), ts=NOW), secret="bad" * 12)
    finally:
        logger.remove(sink)
    assert lines and not [ln for ln in lines if "SECRET" in ln or "sub.example" in ln]
