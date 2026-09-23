"""Dispatcher assembly of release 3.0 (no I/O, no preflight).

app.main.setup_dispatcher calls build_dispatcher(bot, storage=<Redis or
memory>) and then loads the blocklist; tests/flows call it directly with a
recording bot session.
"""
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.logger import logger


def build_dispatcher(bot: Bot, storage=None, container=None) -> Dispatcher:
    """Собирает диспетчер без I/O (app.main и tests/flows).

    3.0: контейнер портов (app.container), внешние middleware DI, техработ и
    алиасов старых колбэков, порядок роутеров из app.bot.routers.ROUTERS.
    После cutover 3.0 роутеров 2.x нет: старые колбэки переписывает слой
    алиасов, неизвестные ловит r3_fallback.
    """
    dp = Dispatcher(storage=storage if storage is not None else MemoryStorage())
    logger.info("Диспетчер создан")

    from app.bot.legacy_aliases import LegacyAliasMiddleware
    from app.bot.middlewares.di import DIMiddleware
    from app.bot.middlewares.maintenance import MaintenanceMiddleware
    from app.bot.routers import include_routers, new_routers
    from app.container import build_container, set_container

    if container is None:
        container = build_container(bot)
    set_container(container)

    # 3.0: порты в данные хендлеров (для всех типов апдейтов)
    dp.update.outer_middleware(DIMiddleware(container))
    # 3.0: техработы (no-op, пока флаг не включен) — до алиасов и роутинга
    maintenance_mw = MaintenanceMiddleware(container.maintenance)
    dp.message.outer_middleware(maintenance_mw)
    dp.callback_query.outer_middleware(maintenance_mw)
    # 3.0: старые строки колбэков -> упакованные колбэки (если есть новый хендлер)
    dp.callback_query.outer_middleware(LegacyAliasMiddleware(new_routers))

    from app.middlewares.auth import AuthMiddleware
    from app.middlewares.timing import TimingMiddleware
    from app.middlewares.blocklist import BlocklistMiddleware

    # Timing middleware должен быть первым для измерения всего времени выполнения
    dp.message.middleware(TimingMiddleware())
    dp.callback_query.middleware(TimingMiddleware())

    # Blocklist middleware — до Auth, чтобы заблокированные не проходили дальше
    dp.message.middleware(BlocklistMiddleware())
    dp.callback_query.middleware(BlocklistMiddleware())

    # Auth middleware
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    logger.info("Middleware подключены")

    # Порядок: site_login -> роутеры 3.0 (r3_fallback последним) -> глобальный
    # errors-handler. site_login первым: диплинк /start login_* и callback
    # sitelogin: не должны доходить до других роутеров.
    include_routers(dp)
    logger.info("Роутеры подключены")
    return dp
