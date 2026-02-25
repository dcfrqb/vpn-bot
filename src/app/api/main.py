"""
FastAPI приложение для обработки webhook'ов от ЮKassa
"""
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.logger import logger
from app.services.payments.yookassa import process_payment_webhook


# Глобальная переменная для хранения экземпляра бота
bot_instance: Bot = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    global bot_instance
    
    # Инициализация бота при запуске
    logger.info("Инициализация FastAPI приложения для webhook'ов ЮKassa")
    
    if not settings.BOT_TOKEN:
        logger.error("BOT_TOKEN не установлен!")
        raise ValueError("BOT_TOKEN должен быть установлен в переменных окружения")
    
    bot_instance = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    
    try:
        bot_info = await bot_instance.get_me()
        logger.info(f"Бот инициализирован для FastAPI: @{bot_info.username} ({bot_info.first_name})")
    except Exception as e:
        logger.error(f"Ошибка инициализации бота: {e}")
        raise
    
    yield
    
    # Закрытие бота при остановке
    if bot_instance:
        await bot_instance.session.close()
        logger.info("Бот закрыт")


app = FastAPI(
    title="CRS VPN Webhook API",
    description="API для обработки webhook'ов от ЮKassa",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/")
async def root():
    """Корневой эндпоинт для проверки работы API"""
    return {
        "status": "ok",
        "service": "CRS VPN Webhook API",
        "version": "1.0.0"
    }


@app.get("/health")
async def health_check():
    """Health check эндпоинт"""
    return {
        "status": "healthy",
        "bot_initialized": bot_instance is not None
    }


@app.post("/webhook/yookassa")
async def yookassa_webhook(request: Request, x_webhook_secret: str = Header(None, alias="X-Webhook-Secret")):
    """
    Эндпоинт для обработки webhook'ов от ЮKassa
    
    ЮKassa отправляет уведомления о событиях платежей:
    - payment.succeeded - успешный платеж
    - payment.waiting_for_capture - платеж ожидает подтверждения
    - payment.canceled - отмена платежа
    - refund.succeeded - успешный возврат
    
    Защита: если YOOKASSA_WEBHOOK_SECRET задан, требуется заголовок X-Webhook-Secret.
    """
    try:
        # Минимальная защита: X-Webhook-Secret
        secret = settings.YOOKASSA_WEBHOOK_SECRET
        if secret and str(secret).strip():
            if not x_webhook_secret or x_webhook_secret != str(secret).strip():
                logger.warning("Webhook rejected: X-Webhook-Secret mismatch or missing")
                raise HTTPException(status_code=401, detail="Unauthorized")
        else:
            logger.warning("YOOKASSA_WEBHOOK_SECRET not configured — webhook accepts any request (dev only, PROD: set secret)")

        # Получаем данные из запроса
        try:
            data = await request.json()
        except Exception as e:
            logger.error(f"Ошибка при парсинге JSON webhook: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON")
        
        if not data:
            logger.error("Получен пустой webhook")
            raise HTTPException(status_code=400, detail="Empty request body")
        
        event = data.get('event', 'unknown')
        logger.info(f"Получен webhook от ЮKassa: {event}")
        
        # Проверяем, что бот инициализирован
        if not bot_instance:
            logger.error("Бот не инициализирован")
            raise HTTPException(status_code=500, detail="Bot not initialized")
        
        # Обрабатываем webhook
        success = await process_payment_webhook(data, bot_instance)
        
        if success:
            logger.info(f"Webhook успешно обработан: {event}")
            return JSONResponse(
                status_code=200,
                content={"status": "ok", "event": event}
            )
        else:
            logger.warning(f"Ошибка при обработке webhook: {event}")
            return JSONResponse(
                status_code=400,
                content={"status": "error", "event": event, "message": "Error processing webhook"}
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Неожиданная ошибка при обработке webhook ЮKassa: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal server error")

