"""Shim: the Remnawave client moved to app.infra.remnawave.client (3.0 stream B).

``app.remnawave.client`` is an alias of the very same module object, so
``from app.remnawave.client import RemnaClient`` and
``patch("app.remnawave.client.X")`` keep working and see identical state.
Do not add code here; edit app/infra/remnawave/client.py. Removed after the
3.0 cutover once no 2.x module imports the old path.
"""
import sys

from app.infra.remnawave import client as _client

sys.modules[__name__] = _client
