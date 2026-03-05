"""
SubscriptionChecker — отключён.
Подписки управляются только в Remnawave. БД удалена.
"""
from app.logger import logger


class SubscriptionChecker:
    """Заглушка: проверка подписок отключена (Remnawave — источник правды)."""

    def __init__(self, bot, check_interval: int = 3600):
        self.bot = bot
        self.check_interval = check_interval
        self.running = False

    def start(self):
        self.running = True
        logger.info("SubscriptionChecker отключён (подписки в Remnawave)")

    def stop(self):
        self.running = False
