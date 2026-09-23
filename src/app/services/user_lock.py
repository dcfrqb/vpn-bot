"""Shim: moved to app.infra.redis.locks (release 3.0 Foundation).

Alias of the same module object, so patches on either path hit the same code.
Removed by the cutover agent once nothing imports it.
"""
import sys

from app.infra.redis import locks as _locks

sys.modules[__name__] = _locks
