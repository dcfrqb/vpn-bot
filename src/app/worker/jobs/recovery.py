"""Payment recovery job (release 3.0, stream A).

``run(ctx)``: one Fulfillment.recover() sweep: pending payments older than
15 minutes are re-checked with YooKassa, paid-but-not-granted ones are
granted again (idempotent), held payments are left to admins.

Registered as ``payment_recovery`` (every 300 s, TASK_RECOVERY_ENABLED, the
2.x flag, default on). 2.x rows are retried only when 2.x flagged them
(needs_provisioning), see payments.store.stuck_is_recoverable.
"""
from __future__ import annotations

from app.services.money import money


async def run(ctx) -> dict:
    services = money(ctx.container)
    result = await services.fulfillment.recover()
    result["refund_access"] = await services.refunds.recover_access()
    return result
