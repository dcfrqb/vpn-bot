"""Пользовательский Redis-лок для промо и ручных выдач (хотфикс 2.1).

aiogram обрабатывает апдейты параллельно: 5 быстрых /trial проходили проверку
«уже использовал?» одновременно и продлевали доступ 5 раз. Лок SET NX с TTL
сериализует такие действия по юзеру. Это первая линия; вторая — запись факта
использования промокода ДО выдачи (уникальный external_id в payments).

С 3.0 живет в app.infra.redis.locks (старый путь app.services.user_lock
оставлен шимом). Здесь же LeaderLock планировщика (app.worker.scheduler).

Если Redis недоступен, лок не блокирует (fail-open): от двойной выдачи в этом
случае защищает уникальная запись в БД. Примитивы — services/redis_flags.
"""
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.logger import logger
from app.infra.redis.flags import _client, compare_and_delete, set_once

DEFAULT_LOCK_TTL_SECONDS = 120


@asynccontextmanager
async def user_action_lock(
    scope: str, user_id: int, ttl: int = DEFAULT_LOCK_TTL_SECONDS
) -> AsyncIterator[bool]:
    """async with user_action_lock("promo", uid) as acquired: ...

    acquired=False — такое же действие этого юзера уже выполняется.
    """
    key = f"lock:{scope}:{int(user_id)}"
    token = uuid.uuid4().hex
    got = await set_once(key, token, ttl=ttl)
    if got is None:
        logger.warning(f"user_action_lock {key}: redis unavailable, continuing without lock")
    acquired = got is not False
    try:
        yield acquired
    finally:
        if got is True:
            await compare_and_delete(key, token)


_RENEW_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then\n"
    "    return redis.call('expire', KEYS[1], ARGV[2])\n"
    "end\n"
    "return 0\n"
)


class LeaderLock:
    """Лидерство одного процесса (планировщик фоновых задач).

    ensure() на каждом тике: держим лок — продлеваем TTL, не держим — SET NX.
    True — мы лидер; False — лидер другой процесс; None — Redis недоступен
    (вызывающий решает; планировщик в этом случае работает, как в 2.x, где
    фоновые задачи шли без лока в единственном контейнере бота).
    """

    def __init__(self, key: str = "scheduler:leader", ttl: int = 90):
        self.key = key
        self.ttl = int(ttl)
        self.token = uuid.uuid4().hex
        self.held = False

    async def ensure(self) -> "bool | None":
        if self.held:
            try:
                client = _client()
                if client is None:
                    return None
                renewed = await client.eval(_RENEW_SCRIPT, 1, self.key, self.token, str(self.ttl))
                if renewed:
                    return True
                self.held = False
                logger.warning(f"leader lock {self.key}: lost leadership")
            except Exception as e:
                logger.warning(f"leader lock {self.key}: renew failed ({e})")
                return None
        got = await set_once(self.key, self.token, ttl=self.ttl)
        if got:
            self.held = True
            logger.info(f"leader lock {self.key}: acquired")
        return got

    async def release(self) -> None:
        if self.held:
            await compare_and_delete(self.key, self.token)
            self.held = False
