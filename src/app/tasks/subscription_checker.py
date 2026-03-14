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

    async def _run_once(self, label: str = "") -> None:
        prefix = f"SubscriptionChecker[{label}]" if label else "SubscriptionChecker"
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

        # Stage C: expiry notifications
        try:
            from app.tasks.expiry_notifier import check_expiry_notifications
            await check_expiry_notifications(self.bot)
        except Exception as e:
            logger.error(f"{prefix}: error during expiry notifications: {e}")
