"""2.x jobs still running in 3.0, behaviour unchanged (retire in 3.0.1).

- expiry_notifier: 2.x «подписка истекает» (app.tasks.expiry_notifier), gate
  TASK_EXPIRY_NOTIFIER_ENABLED (default on) and only while the 3.0
  ``reminders`` job is off (TASK_REMINDERS_ENABLED), so users never get both.
  The dedup keys are shared, switching over resends nothing.
- reconciler: 2.x Remnawave reconciler shallow scan
  (app.tasks.remnawave_reconciler), gate TASK_RECONCILER_ENABLED (default on).
  Turn it off once ``panel_sync`` has run for a week.

The payment recovery stage of the 2.x SubscriptionChecker is the 3.0
``payment_recovery`` job (same TASK_RECOVERY_ENABLED flag).
"""
from __future__ import annotations

from typing import Any, Optional

from app.logger import logger

_reconciler: Optional[Any] = None


def expiry_notifier_enabled() -> bool:
    from app.config import task_enabled

    return task_enabled("EXPIRY_NOTIFIER") and not task_enabled("REMINDERS")


async def expiry_notifier(ctx) -> None:
    from app.tasks.expiry_notifier import check_expiry_notifications

    await check_expiry_notifications(ctx.bot)


async def reconciler(ctx) -> None:
    global _reconciler
    from app.tasks.remnawave_reconciler import RemnawaveReconciler

    if _reconciler is None or _reconciler.bot is not ctx.bot:
        _reconciler = RemnawaveReconciler(ctx.bot)
    result = await _reconciler.run_once()
    logger.debug(f"legacy reconciler ({ctx.label}): {result}")
