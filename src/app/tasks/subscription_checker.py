"""Периодическая задача для проверки подписок и отправки уведомлений"""
import asyncio
from datetime import datetime
from typing import Optional

from app.logger import logger
from app.services.subscriptions import check_expired_subscriptions, process_subscription_notifications


class SubscriptionChecker:
    """Класс для периодической проверки подписок"""
    
    def __init__(self, bot, check_interval: int = 3600):
        """Инициализирует проверку подписок"""
        self.bot = bot
        self.check_interval = check_interval
        self.running = False
        self._task: Optional[asyncio.Task] = None
    
    async def _check_loop(self):
        """Основной цикл проверки"""
        logger.info(f"Запуск периодической проверки подписок (интервал: {self.check_interval} сек)")
        
        while self.running:
            try:
                logger.info("Начинаю проверку подписок")
                
                # Очищаем истекший кэш перед проверкой
                try:
                    from app.services.cache import cleanup_expired_cache
                    await cleanup_expired_cache()
                except Exception as e:
                    logger.debug(f"Ошибка при очистке кэша: {e}")
                
                expired_result = await check_expired_subscriptions()
                logger.info(
                    f"Проверка истекших подписок: "
                    f"деактивировано {expired_result['expired']}, "
                    f"ошибок {expired_result['errors']}"
                )
                
                notification_result = await process_subscription_notifications(self.bot)
                logger.info(
                    f"Уведомления: "
                    f"3 дня - {notification_result['notified_3d']}, "
                    f"1 день - {notification_result['notified_1d']}, "
                    f"сегодня - {notification_result['notified_0d']}, "
                    f"ошибок - {notification_result['errors']}"
                )
                
                try:
                    from app.services.payments.recovery import retry_needs_provisioning, recheck_pending_payments
                    prov_result = await retry_needs_provisioning(self.bot)
                    if prov_result["processed"]:
                        logger.info(f"Payment recovery (needs_provisioning): processed={prov_result['processed']} succeeded={prov_result['succeeded']} errors={prov_result['errors']}")
                    pend_result = await recheck_pending_payments(self.bot)
                    if pend_result["checked"]:
                        logger.info(f"Payment recovery (pending recheck): checked={pend_result['checked']} updated={pend_result['updated']} errors={pend_result['errors']}")
                except Exception as e:
                    logger.debug(f"Payment recovery error: {e}")
                
                logger.info(f"Проверка завершена. Следующая проверка через {self.check_interval} сек")
                
            except Exception as e:
                logger.error(f"Ошибка в цикле проверки подписок: {e}")
            
            await asyncio.sleep(self.check_interval)
    
    def start(self):
        """Запускает периодическую проверку"""
        if self.running:
            logger.warning("Проверка подписок уже запущена")
            return
        
        self.running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info("Периодическая проверка подписок запущена")
    
    def stop(self):
        """Останавливает периодическую проверку"""
        if not self.running:
            return
        
        self.running = False
        if self._task:
            self._task.cancel()
        logger.info("Периодическая проверка подписок остановлена")
    
    async def run_once(self):
        """Запускает одну проверку для тестирования"""
        logger.info("Запуск разовой проверки подписок")
        
        expired_result = await check_expired_subscriptions()
        notification_result = await process_subscription_notifications(self.bot)
        
        return {
            "expired": expired_result,
            "notifications": notification_result
        }



