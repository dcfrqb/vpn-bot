"""
SubscriptionChecker — периодическое восстановление платежей после рестарта.

Запускается при старте бота и:
1. Немедленно выполняет recovery (recheck_pending_payments + retry_needs_provisioning)
   — это страховка от потери auto-recheck задач при рестарте контейнера.
2. Повторяет recovery каждые check_interval секунд (по умолчанию 900 = 15 мин).

Статус подписок управляется только в Remnawave. БД не является источником истины
для статуса, но используется для хранения платежей и provisioning-флагов.
"""
import asyncio
from app.logger import logger


class SubscriptionChecker:
    """Периодически перепроверяет pending-платежи и retry provisioning."""

    def __init__(self, bot, check_interval: int = 900):
        self.bot = bot
        self.check_interval = check_interval
        self.running = False
        self._task: asyncio.Task | None = None

    def start(self):
        self.running = True
        self._task = asyncio.create_task(self._run())
        logger.info(
            f"SubscriptionChecker: recovery loop started (interval={self.check_interval}s)"
        )

    def stop(self):
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run(self):
        # Немедленный прогон при старте: ловим платежи, потерянные при предыдущем рестарте.
        await self._run_once(label="startup")

        while self.running:
            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            if not self.running:
                break
            await self._run_once(label="periodic")

    @staticmethod
    def any_stage_enabled() -> bool:
        from app.config import task_enabled
        return any(task_enabled(n) for n in ("RECOVERY", "EXPIRY_NOTIFIER", "RECONCILER"))

    async def _run_once(self, label: str = "") -> None:
        from app.config import task_enabled

        prefix = f"SubscriptionChecker[{label}]" if label else "SubscriptionChecker"
        if task_enabled("RECOVERY"):
            await self._run_recovery(prefix)
        else:
            logger.debug(f"{prefix}: recovery disabled by config")
        if task_enabled("EXPIRY_NOTIFIER"):
            if task_enabled("REMINDERS"):
                # 3.0: напоминания шлет задача reminders (worker/jobs/reminders.py)
                # с теми же dedup-ключами; старый нотификатор молчит, чтобы не было дублей.
                logger.debug(f"{prefix}: expiry notifier replaced by 3.0 reminders")
            else:
                await self._run_expiry(prefix)
        if task_enabled("RECONCILER"):
            await self._run_reconciler(prefix)

    async def _run_recovery(self, prefix: str) -> None:
        try:
            from app.services.payments.recovery import (
                recheck_pending_payments,
                retry_needs_provisioning,
            )
        except ImportError:
            logger.debug(f"{prefix}: recovery not available (legacy mode disabled?)")
            return

        try:
            r_pending = await recheck_pending_payments(self.bot)
            r_prov = await retry_needs_provisioning(self.bot)
            # Логируем только если что-то произошло, чтобы не засорять логи
            if r_pending.get("updated") or r_prov.get("succeeded") or r_pending.get("errors") or r_prov.get("errors"):
                logger.info(
                    f"{prefix}: pending_recheck={r_pending} provisioning_retry={r_prov}"
                )
            else:
                logger.debug(
                    f"{prefix}: nothing to do "
                    f"(checked={r_pending.get('checked',0)} processed={r_prov.get('processed',0)})"
                )
        except Exception as e:
            logger.error(f"{prefix}: error during recovery: {e}")

    async def _run_expiry(self, prefix: str) -> None:
        # Stage C: expiry notifications
        try:
            from app.tasks.expiry_notifier import check_expiry_notifications
            await check_expiry_notifications(self.bot)
        except Exception as e:
            logger.error(f"{prefix}: error during expiry notifications: {e}")

    async def _run_reconciler(self, prefix: str) -> None:
        # Stage D: Remnawave reconciler shallow scan
        # Дополняет recovery.retry_needs_provisioning: тот ходит по платежам,
        # этот — по подпискам, через provisioning_state. Покрывает кейсы, когда
        # платеж был провижионен идемпотентно, а Remnawave ушел в desync.
        try:
            from app.tasks.remnawave_reconciler import RemnawaveReconciler
            if not hasattr(self, "_reconciler_singleton"):
                self._reconciler_singleton = RemnawaveReconciler(self.bot)
            await self._reconciler_singleton.run_once()
        except Exception as e:
            logger.error(f"{prefix}: error during reconciler: {e}")
