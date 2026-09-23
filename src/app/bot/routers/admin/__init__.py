"""3.0 admin routers, one module per area (see app.bot.routers.NEW_ROUTER_MODULES).

Admin-only access is enforced per router by the owning stream
(e.g. ``router.callback_query.filter(IsAdmin())``).
"""
