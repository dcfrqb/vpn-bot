"""Redis primitives (release 3.0 Foundation).

- flags.py - set-once flags, get/set/delete, counters, once-markers (dedup).
- locks.py - per-user action lock, scheduler leader lock.
- cache.py - small JSON cache (status cache etc.).

One policy everywhere: Redis unavailable -> the primitive returns None/False
and logs a warning; the caller decides (usually fail-open, the DB is the
second line). The client is looked up on every call through
``app.services.cache.get_redis_client`` so tests can patch that single name.
"""


def get_client():
    from app.services.cache import get_redis_client

    return get_redis_client()
