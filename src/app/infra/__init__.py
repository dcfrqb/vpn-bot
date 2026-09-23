"""Infrastructure adapters (release 3.0): Redis, Remnawave, YooKassa, Telegram Stars.

Adapters talk to the outside world and return domain DTOs. Services depend on
ports (app.services.ports), never on these modules directly; the wiring lives
in app.container.
"""
