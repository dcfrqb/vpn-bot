import asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage, DefaultKeyBuilder
import redis.asyncio as redis

from app.config import settings
from app.logging import logger
from app.routers import start, payments

async def run_polling():
    bot = Bot(token=settings.BOT_TOKEN, parse_mode="HTML")
    r = redis.from_url(settings.REDIS_URL)
    dp = Dispatcher(storage=RedisStorage(r, key_builder=DefaultKeyBuilder(with_bot_id=True)))
    dp.include_routers(start.router, payments.router)
    logger.info("Bot is starting (polling)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(run_polling())