"""Запуск фоновых задач бота по флагам BACKGROUND_TASKS_ENABLED / TASK_*.

С 3.0 делегирует единому планировщику app.worker.scheduler (один цикл,
лидер-лок в Redis, реестр задач build_jobs). Задачи 2.x те же и под теми же
флагами: SubscriptionChecker (recovery, expiry notifier, reconciler),
Sun718RevertTask, дорассылка после рестарта.
"""


async def start_background_tasks(bot, container=None):
    """Запускает планировщик. Возвращает объект с .stop()."""
    if container is None:
        try:
            from app.container import get_container

            container = get_container()
        except RuntimeError:
            container = None
    from app.worker.scheduler import start_scheduler

    return await start_scheduler(bot, container)
