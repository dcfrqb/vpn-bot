"""3.0 Foundation: webhook API assembly (routes moved, shims kept)."""
from fastapi.testclient import TestClient


def test_api_main_is_a_shim_over_app():
    from app.api import app as app_module
    from app.api import main as api_main
    from app.api.routes import yookassa

    assert api_main.app is app_module.app
    assert api_main._get_client_ip is yookassa._get_client_ip
    assert api_main.bot_instance is yookassa.bot_instance


def test_uvicorn_target_unchanged():
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "src/app/api/server.py").read_text()
    assert '"app.api.main:app"' in src  # server.py runs preflight on import, read the source
    compose = (pathlib.Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
    assert "python3 -m app.api.server" in compose and "python3 -m app.main" in compose


def test_routes_present():
    from app.api.main import app

    paths = {(r.path, tuple(sorted(getattr(r, "methods", []) or []))) for r in app.routes}
    for p in [("/webhook/yookassa", ("POST",)), ("/webhook/remnawave", ("POST",)), ("/health", ("GET",)),
              ("/", ("GET",)), ("/internal/site/users/{telegram_id}/profile", ("GET",)),
              ("/internal/site/health", ("GET",))]:
        assert p in paths, p
    assert app.docs_url is None and app.openapi_url is None


def test_remnawave_webhook_is_501_stub():
    from app.api.main import app

    r = TestClient(app).post("/webhook/remnawave", json={"event": "user.expired"})
    assert r.status_code == 501 and r.json() == {"status": "not_implemented"}
