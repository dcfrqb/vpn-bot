import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.logging import logger


async def run_polling():
    logger.info("🚀 Инициализация бота...")
    
    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    logger.info("✅ Бот создан успешно")
    
    # r = redis.from_url(settings.REDIS_URL)
    # dp = Dispatcher(storage=RedisStorage(r, key_builder=DefaultKeyBuilder(with_bot_id=True)))
    dp = Dispatcher()
    logger.info("✅ Диспетчер создан")

    # Include only the start router for now
    from app.routers.start import router as start_router
    dp.include_router(start_router)
    logger.info("✅ Роутеры подключены")

    # Получаем информацию о боте
    try:
        bot_info = await bot.get_me()
        logger.info(f"🤖 Бот запущен: @{bot_info.username} ({bot_info.first_name})")
        logger.info(f"🆔 ID бота: {bot_info.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка получения информации о боте: {e}")

    logger.info("🔄 Запуск polling...")
    logger.info("=" * 50)
    logger.info("✅ БОТ РАБОТАЕТ! Нажмите Ctrl+C для остановки")
    logger.info("=" * 50)
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_polling())