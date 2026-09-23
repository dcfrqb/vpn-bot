"""Shim: the plan catalog moved to app.domain.plans (release 3.0 Foundation).

`app.core.plans` is kept as an alias of the very same module object, so
`from app.core.plans import X`, `from app.core import plans` and
`patch("app.core.plans.X")` keep working and see identical state.
Do not add code here; edit app/domain/plans.py. Kept after the 3.0 cutover.
"""
import sys

from app.domain import plans as _plans

sys.modules[__name__] = _plans
