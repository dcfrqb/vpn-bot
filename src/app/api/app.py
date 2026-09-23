"""
FastAPI приложение webhook-API (release 3.0): сборка маршрутов.

Маршруты:
  - app.api.routes.yookassa  — POST /webhook/yookassa (перенесен 1-в-1, поток A);
  - app.api.routes.remnawave — POST /webhook/remnawave (501 до потока C);
  - app.api.routes.health    — GET / и GET /health;
  - app.api.internal_site    — /internal/site/* для сайта (контракт не менялся).
FROZEN seam: новые маршруты подключаются здесь коммитом оркестратора.
Процесс запускается как раньше: app.api.server -> uvicorn "app.api.main:app".
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.logger import logger
from app.api.routes import yookassa as yookassa_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    
    logger.info("Инициализация FastAPI приложения для webhook'ов ЮKassa")

    if not settings.BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен!")
        raise ValueError("BOT_TOKEN должен быть установлен в переменных окружения")

    bot_instance = yookassa_routes.bot_instance = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )

    try:
        bot_info = await bot_instance.get_me()
        logger.info(f"Бот инициализирован для FastAPI: @{bot_info.username} ({bot_info.first_name})")
    except Exception as e:
        logger.error(f"Ошибка инициализации бота: {e}")
        raise

    if not settings.YOOKASSA_WEBHOOK_SECRET:
        logger.error(
            "SECURITY: YOOKASSA_WEBHOOK_SECRET не задан — "
            "webhook API работает без проверки подписи! "
            "Установите YOOKASSA_WEBHOOK_SECRET в .env"
        )

    from app.container import build_container, set_container

    set_container(build_container(bot_instance))

    yield

    # Закрытие бота при остановке
    if bot_instance:
        await bot_instance.session.close()
        logger.info("Бот закрыт")


app = FastAPI(
    title="CRS VPN Webhook API",
    description="API для обработки webhook'ов от ЮKassa",
    version="2.0.0",
    lifespan=lifespan,
    # Хотфикс 2.1: /docs, /redoc, /openapi.json были открыты в интернет.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Внутренний API для сайта: /internal/site/* (только docker-сеть, см. internal_site.py).
from app.api.internal_site import (  # noqa: E402
    InternalApiError,
    internal_api_error_handler,
    router as internal_site_router,
)

app.add_exception_handler(InternalApiError, internal_api_error_handler)
app.include_router(internal_site_router)


from app.api.routes import health as health_routes  # noqa: E402
from app.api.routes import remnawave as remnawave_routes  # noqa: E402

app.include_router(health_routes.router)
app.include_router(yookassa_routes.router)
app.include_router(remnawave_routes.router)
