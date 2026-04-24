import asyncio
import os
import signal

from app.utils.preflight import run_preflight_bot

# Preflight: проверка обязательных env до импорта тяжёлых модулей
run_preflight_bot()

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.config import settings
from app.logger import logger


_shutdown_event: asyncio.Event | None = None


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> None:
    """Регистрирует SIGTERM/SIGINT для graceful shutdown.
    Вызов event.set() пробуждает run_webhook()/run_polling(), они корректно закрывают ресурсы.
    """
    def _handler(sig: signal.Signals) -> None:
        logger.info(f"Получен сигнал {sig.name}, начинаем graceful shutdown")
        event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handler, sig)
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler — запасной путь через signal.signal
            signal.signal(sig, lambda *_: event.set())

# Используем uvloop для лучшей производительности event loop
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    logger.info("uvloop включен для улучшения производительности")
except ImportError:
    logger.debug("uvloop не установлен, используется стандартный event loop")


def get_storage():
    """Получает storage для диспетчера Redis или Memory"""
    if settings.REDIS_URL:
        try:
            import redis.asyncio as redis
            from aiogram.fsm.storage.redis import RedisStorage
            from aiogram.fsm.storage.redis import DefaultKeyBuilder
            
            r = redis.from_url(settings.REDIS_URL, decode_responses=True)
            storage = RedisStorage(redis=r, key_builder=DefaultKeyBuilder(with_bot_id=True))
            logger.info("Redis storage подключен")
            return storage
        except Exception as e:
            logger.warning(f"Не удалось подключить Redis storage: {e}. Используется Memory storage")
            return MemoryStorage()
    else:
        logger.info("Redis URL не указан, используется Memory storage")
        return MemoryStorage()


async def setup_dispatcher(bot: Bot) -> Dispatcher:
    """Настраивает и возвращает диспетчер"""
    storage = get_storage()
    dp = Dispatcher(storage=storage)
    logger.info("Диспетчер создан")

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

    from app.routers.start import router as start_router
    from app.routers.admin import router as admin_router
    from app.routers.ui import router as ui_router
    from app.routers.legacy_callbacks import router as legacy_router

    # UI router должен быть первым для обработки ui: callbacks
    dp.include_router(ui_router)
    dp.include_router(start_router)

    from app.legacy.routers.payments import router as legacy_payments_router
    from app.routers.admin_broadcast import router as admin_broadcast_router
    dp.include_router(legacy_payments_router)
    dp.include_router(admin_broadcast_router)
    # ВАЖНО: app.routers.payments (payments.py) НЕ регистрируется намеренно —
    # он содержит дублирующий обработчик pay_yookassa_,
    # что конфликтует с legacy_payments_router.
    logger.info("Режим legacy: YooKassa + БД")

    dp.include_router(admin_router)
    dp.include_router(legacy_router)  # В конце для обратной совместимости

    # Глобальный errors-handler (RetryAfter/Forbidden/BadRequest) — включаем последним,
    # чтобы он ловил ошибки всех роутеров выше.
    from app.middlewares.tg_errors import router as errors_router
    dp.include_router(errors_router)
    logger.info("Роутеры подключены")

    from app.middlewares.blocklist import load_blocklist_from_redis
    await load_blocklist_from_redis()

    return dp


async def run_polling():
    """Запускает бота в режиме polling"""
    logger.info("Инициализация бота (polling режим)")

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    logger.info("Бот создан успешно")

    dp = await setup_dispatcher(bot)

    try:
        bot_info = await bot.get_me()
        logger.info(f"Бот запущен: @{bot_info.username} ({bot_info.first_name})")
        logger.info(f"ID бота: {bot_info.id}")
    except Exception as e:
        logger.error(f"Ошибка получения информации о боте: {e}")

    from app.tasks.subscription_checker import SubscriptionChecker
    subscription_checker = SubscriptionChecker(bot, check_interval=3600)
    subscription_checker.start()
    logger.info("Периодическая проверка подписок запущена (интервал 1 час)")

    # Broadcast: подхватываем рассылки, которые не закончились до рестарта
    try:
        from app.services.broadcast import resume_unfinished_broadcasts
        resumed = await resume_unfinished_broadcasts(bot)
        if resumed:
            logger.info(f"Resumed {resumed} unfinished broadcast(s) после рестарта")
    except Exception as _e:
        logger.warning(f"broadcast resume failed: {_e}")

    logger.info("Запуск polling")
    logger.info("=" * 50)
    logger.info("БОТ РАБОТАЕТ! Нажмите Ctrl+C для остановки")
    logger.info("=" * 50)
    
    try:
        # Принудительная очистка перед запуском polling
        # Удаляем webhook (если был установлен) и ждем, чтобы старое соединение закрылось
        try:
            webhook_info = await bot.get_webhook_info()
            if webhook_info.url:
                logger.info(f"Найден установленный webhook: {webhook_info.url}. Удаляем...")
                await bot.delete_webhook(drop_pending_updates=True)
                logger.info("Webhook удален. Ждем 5 секунд для закрытия соединений...")
                await asyncio.sleep(5)
        except Exception as e:
            logger.debug(f"Ошибка при проверке/удалении webhook: {e}")
        
        # Обработка конфликтов и ошибок polling
        max_retries = 10
        retry_delay = 10.0  # Увеличена начальная задержка
        
        for attempt in range(max_retries):
            try:
                await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
                break  # Успешный запуск
            except Exception as e:
                error_msg = str(e)
                if "Conflict" in error_msg or "getUpdates" in error_msg:
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Конфликт при запуске polling (попытка {attempt + 1}/{max_retries}). "
                            f"Повтор через {retry_delay:.1f} секунд..."
                        )
                        # Увеличиваем задержку и пытаемся удалить webhook снова
                        try:
                            await bot.delete_webhook(drop_pending_updates=True)
                        except:
                            pass
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 1.5, 60.0)  # Экспоненциальная задержка, макс 60 сек
                    else:
                        logger.error(f"Не удалось запустить polling после {max_retries} попыток: {e}")
                        logger.error("Рекомендуется перейти на webhook режим для избежания конфликтов")
                        raise
                else:
                    logger.error(f"Ошибка при запуске polling: {e}")
                    raise
    finally:
        subscription_checker.stop()
        await bot.session.close()


async def run_webhook():
    """Запускает бота в режиме webhook"""
    if not settings.TELEGRAM_WEBHOOK_URL:
        raise ValueError("TELEGRAM_WEBHOOK_URL должен быть указан в конфигурации для webhook режима")

    logger.info("Инициализация бота (webhook режим)")

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    logger.info("Бот создан успешно")

    dp = await setup_dispatcher(bot)

    try:
        bot_info = await bot.get_me()
        logger.info(f"Бот запущен: @{bot_info.username} ({bot_info.first_name})")
        logger.info(f"ID бота: {bot_info.id}")
    except Exception as e:
        logger.error(f"Ошибка получения информации о боте: {e}")

    from app.tasks.subscription_checker import SubscriptionChecker
    subscription_checker = SubscriptionChecker(bot, check_interval=3600)
    subscription_checker.start()
    logger.info("Периодическая проверка подписок запущена (интервал 1 час)")

    # Broadcast: подхватываем рассылки, которые не закончились до рестарта
    try:
        from app.services.broadcast import resume_unfinished_broadcasts
        resumed = await resume_unfinished_broadcasts(bot)
        if resumed:
            logger.info(f"Resumed {resumed} unfinished broadcast(s) после рестарта")
    except Exception as _e:
        logger.warning(f"broadcast resume failed: {_e}")

    # TELEGRAM_WEBHOOK_URL должен содержать полный URL включая путь /webhook
    webhook_base_url = settings.TELEGRAM_WEBHOOK_URL.rstrip('/')
    # Если путь /webhook уже есть в URL, используем как есть, иначе добавляем
    if webhook_base_url.endswith('/webhook'):
        webhook_url = webhook_base_url
        webhook_path = "/webhook"
    else:
        webhook_url = f"{webhook_base_url}/webhook"
        webhook_path = "/webhook"
    
    logger.info(f"TELEGRAM_WEBHOOK_URL из настроек: {settings.TELEGRAM_WEBHOOK_URL}")
    logger.info(f"Итоговый Telegram webhook URL: {webhook_url}")
    logger.info(f"Webhook path для обработчика: {webhook_path}")
    
    secret_token = settings.BOT_SECRET_TOKEN or None
    await bot.set_webhook(webhook_url, secret_token=secret_token)
    if secret_token:
        logger.info(f"Telegram webhook установлен с BOT_SECRET_TOKEN: {webhook_url}")
    else:
        logger.warning("BOT_SECRET_TOKEN не задан — Telegram webhook без проверки секрета")
        logger.info(f"Telegram webhook установлен: {webhook_url}")

    app = web.Application()
    app["bot"] = bot

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=secret_token,
    )
    webhook_requests_handler.register(app, path=webhook_path)
    setup_application(app, dp, bot=bot)
    
    # YooKassa webhook отключён — платежи обрабатываются вручную администратором
    
    logger.info("=" * 50)
    logger.info("БОТ РАБОТАЕТ В WEBHOOK РЕЖИМЕ!")
    logger.info(f"Webhook URL: {webhook_url}")
    logger.info("=" * 50)
    
    shutdown_event = asyncio.Event()
    _install_signal_handlers(asyncio.get_running_loop(), shutdown_event)

    bind_host = os.getenv("BOT_WEBHOOK_BIND_HOST", "0.0.0.0")
    bind_port = int(os.getenv("BOT_WEBHOOK_BIND_PORT", "8000"))

    try:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=bind_host, port=bind_port)
        await site.start()
        logger.info(f"HTTP сервер запущен на {bind_host}:{bind_port}")

        await shutdown_event.wait()
        logger.info("shutdown_event получен, останавливаем...")
    finally:
        # Graceful: сначала отключаем источники новых задач, потом закрываем ресурсы.
        try:
            subscription_checker.stop()
        except Exception as _e:
            logger.warning(f"subscription_checker.stop() failed: {_e}")
        try:
            # Drain: даём in-flight broadcast-ам/хендлерам шанс завершиться (до 10с).
            from app.services.broadcast import shutdown_broadcast_worker
            await asyncio.wait_for(shutdown_broadcast_worker(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("broadcast worker не завершился за 10с, прерываем")
        except Exception as _e:
            logger.debug(f"broadcast worker shutdown: {_e}")
        try:
            await runner.cleanup()
        except Exception:
            pass
        await bot.delete_webhook()
        await bot.session.close()
        logger.info("Webhook удален, бот остановлен")


if __name__ == "__main__":
    if settings.TELEGRAM_WEBHOOK_URL:
        asyncio.run(run_webhook())
    else:
        asyncio.run(run_polling())