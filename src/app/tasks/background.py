"""Запуск фоновых задач бота по флагам BACKGROUND_TASKS_ENABLED / TASK_*.

Вынесено из main.py (фикс-раунд 1), чтобы отладочный бот, который ходит в
боевую панель Remnawave, можно было поднять без recovery, реконсилера,
уведомлений об истечении, отката sun718 и дорассылок.
"""
from app.config import settings
from app.logger import logger


class _NoopTask:
    def stop(self):
        return None


async def start_background_tasks(bot):
    """Запускает фоновые задачи по флагам BACKGROUND_TASKS_ENABLED / TASK_*.

    Возвращает объект с .stop() (SubscriptionChecker или заглушку).
    """
    from app.config import task_enabled
    from app.tasks.subscription_checker import SubscriptionChecker

    if not settings.BACKGROUND_TASKS_ENABLED:
        logger.warning("BACKGROUND_TASKS_ENABLED=false: фоновые задачи НЕ запущены")

    subscription_checker = _NoopTask()
    if SubscriptionChecker.any_stage_enabled():
        subscription_checker = SubscriptionChecker(bot, check_interval=3600)
        subscription_checker.start()
        logger.info("Периодическая проверка подписок запущена (интервал 1 час)")
    else:
        logger.warning("SubscriptionChecker выключен (recovery, expiry notifier, reconciler)")

    if task_enabled("SUN718_REVERT"):
        from app.tasks.sun718_revert import Sun718RevertTask
        sun718_revert_task = Sun718RevertTask(bot, check_interval=3600)
        sun718_revert_task.start()
    else:
        logger.warning("Sun718RevertTask выключен")

    # Broadcast: подхватываем рассылки, которые не закончились до рестарта
    if task_enabled("BROADCAST_RESUME"):
        try:
            from app.services.broadcast import resume_unfinished_broadcasts
            resumed = await resume_unfinished_broadcasts(bot)
            if resumed:
                logger.info(f"Resumed {resumed} unfinished broadcast(s) после рестарта")
        except Exception as _e:
            logger.warning(f"broadcast resume failed: {_e}")
    else:
        logger.warning("Дослать рассылки после рестарта: выключено")
    return subscription_checker
