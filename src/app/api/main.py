"""Shim: webhook-API moved to app.api.app + app.api.routes (release 3.0).

Kept so compose/uvicorn keep using "app.api.main:app" and old imports work.
Patch the real module in tests: app.api.routes.yookassa (_get_client_ip,
_webhook_rate_limit_ok, bot_instance, ...). Kept after the 3.0 cutover.
"""
from app.api.app import app, lifespan  # noqa: F401
from app.api.routes.health import health_check, root  # noqa: F401
from app.api.routes.yookassa import (  # noqa: F401
    DOCKER_GATEWAY_TOKEN,
    _WEBHOOK_RATE_LIMIT_PER_MIN,
    _YOOKASSA_NETWORKS,
    _docker_default_gateway,
    _get_client_ip,
    _is_yookassa_ip,
    _parse_trusted_proxies,
    _trusted_proxy_networks,
    _webhook_rate_limit_ok,
    yookassa_webhook,
)


def __getattr__(name):
    # bot_instance is assigned at startup: always read the live value.
    if name == "bot_instance":
        from app.api.routes import yookassa

        return yookassa.bot_instance
    raise AttributeError(name)
