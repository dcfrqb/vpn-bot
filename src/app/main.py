import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from app.config import settings
from app.logger import logger

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
    
    # Timing middleware должен быть первым для измерения всего времени выполнения
    dp.message.middleware(TimingMiddleware())
    dp.callback_query.middleware(TimingMiddleware())
    
    # Auth middleware
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    logger.info("Middleware подключены")

    from app.routers.start import router as start_router
    from app.routers.payments import router as payments_router
    from app.routers.admin import router as admin_router
    from app.routers.crypto_payments import router as crypto_payments_router
    from app.routers.ui import router as ui_router
    from app.routers.legacy_callbacks import router as legacy_router
    
    # UI router должен быть первым для обработки ui: callbacks
    dp.include_router(ui_router)
    # Legacy router должен быть последним (catch-all для старых форматов)
    dp.include_router(start_router)
    dp.include_router(payments_router)
    dp.include_router(crypto_payments_router)
    dp.include_router(admin_router)
    dp.include_router(legacy_router)  # В конце для обратной совместимости
    logger.info("Роутеры подключены")

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
    logger.info("Периодическая проверка подписок запущена")
    
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
    logger.info("Периодическая проверка подписок запущена")
    
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
    
    await bot.set_webhook(webhook_url)
    logger.info(f"Telegram webhook установлен: {webhook_url}")
    
    app = web.Application()
    app["bot"] = bot
    
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=webhook_path)
    setup_application(app, dp, bot=bot)
    
    from app.routers.payments import yookassa_webhook_handler
    app.router.add_post("/webhook/yookassa", yookassa_webhook_handler)
    logger.info("Webhook endpoint для YooKassa зарегистрирован: /webhook/yookassa")
    
    logger.info("=" * 50)
    logger.info("БОТ РАБОТАЕТ В WEBHOOK РЕЖИМЕ!")
    logger.info(f"Webhook URL: {webhook_url}")
    logger.info("=" * 50)
    
    try:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=8000)
        await site.start()
        logger.info("HTTP сервер запущен на порту 8000")
        
        await asyncio.Event().wait()
    finally:
        subscription_checker.stop()
        await bot.delete_webhook()
        await bot.session.close()
        logger.info("Webhook удален, бот остановлен")


if __name__ == "__main__":
    if settings.TELEGRAM_WEBHOOK_URL:
        asyncio.run(run_webhook())
    else:
        asyncio.run(run_polling())