"""Payment recovery job (release 3.0, stream A).

``run(ctx)``: one Fulfillment.recover() sweep: pending payments older than
15 minutes are re-checked with YooKassa, paid-but-not-granted ones are
granted again (idempotent), held payments are left to admins.

Until the orchestrator registers ``Job("payment_recovery", recovery.run, 300,
flag="RECOVERY")`` (requests/A.md), the same sweep runs inside the 2.x
SubscriptionChecker through services.payments.recovery.retry_needs_provisioning.
"""
from __future__ import annotations

from app.services.money import money


async def run(ctx) -> dict:
    return await money(ctx.container).fulfillment.recover()
