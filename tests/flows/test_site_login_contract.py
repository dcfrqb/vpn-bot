"""Contract snapshot: site_login relay (bot <-> site) through the real Dispatcher.

Recorded: the HTTP requests the bot sends to the site (path, header names,
JSON body) and what the bot shows back (text, buttons, parse_mode).
The site parses/produces these; 3.0 must not change them."""
import pytest

from app.routers import site_login
from tests.contracts.golden import assert_golden


class _Resp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._payload


class FakeSite:
    closed = False

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def post(self, url, json=None, headers=None):
        self.requests.append({"url": url, "json": json, "header_names": sorted(headers or {}),
                              "token_ok": (headers or {}).get("X-Internal-Token") == "site-token"})
        return _Resp(*self.replies.pop(0))


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr("app.config.settings.SITE_INTERNAL_TOKEN", "site-token")
    monkeypatch.setattr("app.config.settings.SITE_INTERNAL_URL", "http://vpn-site-api:8000")
    holder = {}

    def install(replies):
        holder["site"] = FakeSite(replies)
        monkeypatch.setattr(site_login, "_get_session", lambda: holder["site"])
        return holder["site"]

    yield install
    site_login._session = None


def _shown(flow):
    out = []
    for c in flow.session.calls:
        if c.method in ("SendMessage", "EditMessageText", "AnswerCallbackQuery"):
            out.append({"method": c.method, "text": c.params.get("text"), "keyboard": c.keyboard,
                        "parse_mode": c.params.get("parse_mode")})
    return out


async def test_site_login_contract(flow, site):
    payload = "login_" + "Ab3-_" * 4
    fake = site([
        (200, {"text": "Войти на сайт как @tester?", "buttons": [
            {"text": "Да, это я", "data": "sitelogin:ok:" + "x" * 20},
            {"text": "Нет", "data": "sitelogin:no:" + "x" * 20}]}),
        (200, {"text": "Готово, вернись на сайт."}),
    ])
    await flow.send(f"/start {payload}")
    await flow.press("sitelogin:ok:" + "x" * 20)
    assert_golden("site_login_relay", {"requests": fake.requests, "shown": _shown(flow)})


async def test_site_login_site_down_contract(flow, site):
    site([(502, None), (500, None)])
    await flow.send("/start login_" + "Q" * 16)
    await flow.press("sitelogin:ok:abc")
    assert_golden("site_login_site_down", {"shown": _shown(flow)})
