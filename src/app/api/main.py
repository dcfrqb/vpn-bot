"""
FastAPI приложение — webhook ЮKassa.
Полноценная обработка: IP whitelist, идемпотентность, provision.
Webhook используется только как триггер — статус платежа всегда верифицируется через YooKassa API.
"""
import ipaddress

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.logger import logger

# IP-диапазоны YooKassa (https://yookassa.ru/developers/using-api/webhooks)
_YOOKASSA_NETWORKS = [
    ipaddress.ip_network("185.71.76.0/27"),
    ipaddress.ip_network("185.71.77.0/27"),
    ipaddress.ip_network("77.75.153.0/25"),
    ipaddress.ip_network("77.75.154.128/25"),
    ipaddress.ip_network("2a02:5180::/32"),
]


def _get_client_ip(request: Request) -> str | None:
    """Возвращает IP клиента с учётом nginx-прокси."""
    for header in ("CF-Connecting-IP", "X-Real-IP", "X-Forwarded-For"):
        raw = request.headers.get(header)
        if raw:
            return raw.split(",")[0].strip()
    return request.client.host if request.client else None


def _is_yookassa_ip(ip_str: str | None) -> bool:
    """Проверяет, входит ли IP в разрешённые диапазоны YooKassa."""
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in net for net in _YOOKASSA_NETWORKS)
    except ValueError:
        return False


# Глобальная переменная для хранения экземпляра бота
bot_instance: Bot = None

# Идемпотентность webhook обеспечивается через local DB в process_payment_webhook():
# - проверка payment.status (FSM transitions)
# - проверка payment.subscription_id
# - проверка payment_metadata["notified"]
# In-memory set НЕ используется (не устойчив к рестарту).


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    global bot_instance

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

    if not settings.YOOKASSA_WEBHOOK_SECRET:
        logger.error(
            "SECURITY: YOOKASSA_WEBHOOK_SECRET не задан — "
            "webhook API работает без проверки подписи! "
            "Установите YOOKASSA_WEBHOOK_SECRET в .env"
        )

    yield

    # Закрытие бота при остановке
    if bot_instance:
        await bot_instance.session.close()
        logger.info("Бот закрыт")


app = FastAPI(
    title="CRS VPN Webhook API",
    description="API для обработки webhook'ов от ЮKassa",
    version="2.0.0",
    lifespan=lifespan
)


@app.get("/")
async def root():
    """Корневой эндпоинт для проверки работы API"""
    return {
        "status": "ok",
        "service": "CRS VPN Webhook API",
        "version": "2.0.0",
        "mode": "legacy"
    }


@app.get("/health")
async def health_check():
    """Health check: бот + БД + Redis.

    Liveness-часть — факт инициализации бота.
    Readiness-часть — SELECT 1 на Postgres и PING на Redis.
    Если DB или Redis не отвечают — возвращает 503, чтобы orchestrator
    мог вывести контейнер из балансировки.
    """
    status_parts: dict[str, str] = {
        "bot": "ok" if bot_instance is not None else "down",
    }
    overall_ok = bot_instance is not None

    try:
        from sqlalchemy import text as _sa_text

        from app.db.session import SessionLocal
        if SessionLocal:
            async with SessionLocal() as session:
                await session.execute(_sa_text("SELECT 1"))
            status_parts["db"] = "ok"
        else:
            status_parts["db"] = "not_configured"
    except Exception as e:
        status_parts["db"] = f"down: {str(e)[:80]}"
        overall_ok = False

    try:
        from app.services.cache import get_redis_client
        redis_client = get_redis_client()
        if redis_client:
            await redis_client.ping()
            status_parts["redis"] = "ok"
        else:
            status_parts["redis"] = "not_configured"
    except Exception as e:
        status_parts["redis"] = f"down: {str(e)[:80]}"
        overall_ok = False

    body = {
        "status": "healthy" if overall_ok else "degraded",
        "components": status_parts,
        "mode": "legacy",
    }
    if not overall_ok:
        return JSONResponse(status_code=503, content=body)
    return body


# Простой per-IP rate-limiter для webhook (60 req/min).
# YooKassa IP-whitelist выше — это вторая линия защиты от burst/misbehaviour.
_WEBHOOK_RATE_LIMIT_PER_MIN = 60


async def _webhook_rate_limit_ok(client_ip: str | None) -> bool:
    if not client_ip:
        return True
    try:
        from app.services.cache import get_redis_client
        redis_client = get_redis_client()
        if not redis_client:
            return True
        key = f"rl:yk_webhook:{client_ip}"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 60)
        if int(count) > _WEBHOOK_RATE_LIMIT_PER_MIN:
            return False
    except Exception as e:
        logger.debug(f"webhook rate-limit check soft-fail: {e}")
        return True
    return True


@app.post("/webhook/yookassa")
async def yookassa_webhook(request: Request):
    """
    Эндпоинт для обработки webhook'ов от ЮKassa.

    События:
    - payment.succeeded - успешный платеж → provision
    - payment.waiting_for_capture - ожидает подтверждения (игнорируем)
    - payment.canceled - отмена платежа (логируем)
    - refund.succeeded - возврат (логируем)

    Идемпотентность: local DB (payment.subscription_id + metadata.notified).
    """
    # --- SECURITY: IP whitelist ---
    client_ip = _get_client_ip(request)
    if not _is_yookassa_ip(client_ip):
        logger.warning(f"Webhook YooKassa отклонён: неизвестный IP {client_ip!r}")
        raise HTTPException(status_code=403, detail="Forbidden")

    # --- SECURITY: rate-limit (вторая линия защиты за IP whitelist) ---
    if not await _webhook_rate_limit_ok(client_ip):
        logger.warning(f"Webhook YooKassa: rate limit exceeded for {client_ip}")
        raise HTTPException(status_code=429, detail="Too Many Requests")

    # NOTE: YooKassa does not send X-Webhook-Secret headers by default.
    # Security: IP whitelist (above) + direct API verification of every payment inside process_payment_webhook.
    # The webhook payload is treated as a trigger only — status is never trusted from it.

    try:

        # Парсим JSON
        try:
            data = await request.json()
        except Exception as e:
            logger.error(f"Ошибка парсинга JSON webhook: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON")

        if not data:
            logger.error("Получен пустой webhook")
            raise HTTPException(status_code=400, detail="Empty request body")

        event = data.get("event", "unknown")
        payment_obj = data.get("object", {})
        payment_id = payment_obj.get("id", "unknown")

        logger.info(f"Webhook YooKassa: event={event} payment_id={payment_id}")

        # Проверяем бот
        if not bot_instance:
            logger.error("Бот не инициализирован")
            raise HTTPException(status_code=500, detail="Bot not initialized")

        # Обрабатываем в зависимости от события
        if event == "payment.succeeded":
            # Единая точка обработки: local DB + Remnawave + уведомление пользователя
            from app.services.payments.yookassa import process_payment_webhook
            success = await process_payment_webhook(data, bot_instance)
            logger.info(f"Webhook {payment_id}: process_payment_webhook returned success={success}")
            return JSONResponse(status_code=200, content={"status": "ok", "processed": success})

        elif event == "payment.canceled":
            logger.info(f"Webhook {payment_id}: payment canceled")
            from app.services.jsonl_logger import log_payment_event
            log_payment_event(
                event="yookassa_payment_canceled",
                req_id=f"yookassa_{payment_id}",
                payload=data.get("object", {}),
            )
            return JSONResponse(status_code=200, content={"status": "ok", "event": "canceled"})

        elif event == "refund.succeeded":
            logger.info(f"Webhook {payment_id}: refund succeeded")
            from app.services.jsonl_logger import log_payment_event
            log_payment_event(
                event="yookassa_refund",
                req_id=f"yookassa_{payment_id}",
                payload=data.get("object", {}),
            )
            return JSONResponse(status_code=200, content={"status": "ok", "event": "refund"})

        else:
            # waiting_for_capture и другие — просто ACK
            logger.info(f"Webhook {payment_id}: event={event} (ignored)")
            return JSONResponse(status_code=200, content={"status": "ok", "event": event})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка обработки webhook YooKassa: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        # Возвращаем 503, чтобы YooKassa повторила webhook при инфраструктурных сбоях.
        # needs_provisioning=True уже выставлен — SubscriptionChecker подхватит при восстановлении.
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": str(e)[:100]}
        )
