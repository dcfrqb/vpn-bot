"""
FastAPI приложение — webhook ЮKassa для no_db режима.
Полноценная обработка: проверка подписи, идемпотентность, provision.
"""
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from app.config import settings
from app.logger import logger


# Глобальная переменная для хранения экземпляра бота
bot_instance: Bot = None


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

    # Инициализируем SQLite store
    if settings.BOT_MODE != "legacy":
        try:
            from app.nodb.store import init_db
            await init_db()
            logger.info("SQLite store инициализирован для webhook API")
        except Exception as e:
            logger.error(f"Ошибка инициализации SQLite store: {e}")

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
        "mode": settings.BOT_MODE or "no_db"
    }


@app.get("/health")
async def health_check():
    """Health check эндпоинт"""
    return {
        "status": "healthy",
        "bot_initialized": bot_instance is not None,
        "mode": settings.BOT_MODE or "no_db"
    }


async def _process_yookassa_payment(data: dict) -> dict:
    """
    Обрабатывает успешный платеж YooKassa в no_db режиме.
    Возвращает dict с результатом.
    """
    from yookassa.domain.notification import WebhookNotification
    from app.nodb.store import (
        check_webhook_provisioned,
        record_webhook_event,
        mark_webhook_provisioned,
        log_event,
    )
    from app.nodb.services.remnawave_service import provision_tariff
    from app.keyboards import get_subscription_link_keyboard

    notification = WebhookNotification(data)
    payment = notification.object

    if not payment:
        return {"error": "no payment object", "processed": False}

    payment_id = payment.id
    status = payment.status
    amount = float(payment.amount.value)
    metadata = payment.metadata or {}
    tg_id = metadata.get("tg_user_id")
    tariff = metadata.get("tariff") or metadata.get("plan_code")
    period = metadata.get("period_months")

    if not tg_id:
        logger.warning(f"Webhook {payment_id}: missing tg_user_id in metadata")
        return {"error": "missing tg_user_id", "processed": False}

    tg_id = int(tg_id)

    # Записываем событие (idempotency check)
    is_new = await record_webhook_event(
        external_id=payment_id,
        event_type=data.get("event", "unknown"),
        status=status,
        tg_id=tg_id,
        amount=amount,
        processed=True,
    )

    # Проверяем, был ли уже provision
    already_provisioned = await check_webhook_provisioned(payment_id)
    if already_provisioned:
        logger.info(f"Webhook {payment_id}: already provisioned, skipping")
        return {"status": "already_provisioned", "processed": True}

    # Определяем тариф
    if not tariff:
        # Пытаемся определить по сумме
        if amount <= 150:
            tariff = "basic_1"
        elif amount <= 300:
            tariff = "premium_1"
        elif amount <= 500:
            tariff = "premium_3"
        else:
            tariff = "premium_6"

    if period and not tariff.endswith(f"_{period}"):
        tariff = f"{tariff.split('_')[0]}_{period}"

    logger.info(f"Webhook {payment_id}: provisioning tariff={tariff} for tg_id={tg_id}")

    # Provision через Remnawave
    success = await provision_tariff(tg_id, tariff, req_id=f"yookassa_{payment_id}")

    if success:
        await mark_webhook_provisioned(payment_id)
        await log_event(
            event_type="yookassa_provision_success",
            req_id=f"yookassa_{payment_id}",
            tg_id=tg_id,
            payload={"tariff": tariff, "amount": amount, "payment_id": payment_id},
        )

        # Уведомляем пользователя
        try:
            await bot_instance.send_message(
                chat_id=tg_id,
                text=(
                    "✅ <b>Оплата получена!</b>\n\n"
                    "Подписка активирована. Нажмите «Получить ссылку» для настройки VPN."
                ),
                reply_markup=get_subscription_link_keyboard(),
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления пользователя {tg_id}: {e}")

        # Уведомляем админов
        admin_ids = settings.ADMINS if isinstance(settings.ADMINS, list) else []
        for admin_id in admin_ids:
            try:
                await bot_instance.send_message(
                    chat_id=admin_id,
                    text=(
                        f"💰 <b>YooKassa: платеж получен</b>\n\n"
                        f"👤 Telegram ID: <code>{tg_id}</code>\n"
                        f"💵 Сумма: {amount} RUB\n"
                        f"📦 Тариф: {tariff}\n"
                        f"🆔 Payment ID: <code>{payment_id}</code>\n\n"
                        f"✅ Доступ выдан автоматически."
                    ),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📩 Написать пользователю", url=f"tg://user?id={tg_id}")]
                    ]),
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления админа {admin_id}: {e}")

        return {"status": "provisioned", "tariff": tariff, "processed": True}
    else:
        await log_event(
            event_type="yookassa_provision_failed",
            req_id=f"yookassa_{payment_id}",
            tg_id=tg_id,
            payload={"tariff": tariff, "amount": amount, "payment_id": payment_id},
        )
        return {"error": "provision_failed", "processed": False}


@app.post("/webhook/yookassa")
async def yookassa_webhook(request: Request, x_webhook_secret: str = Header(None, alias="X-Webhook-Secret")):
    """
    Эндпоинт для обработки webhook'ов от ЮKassa.

    События:
    - payment.succeeded - успешный платеж → provision
    - payment.waiting_for_capture - ожидает подтверждения (игнорируем)
    - payment.canceled - отмена платежа (логируем)
    - refund.succeeded - возврат (логируем)

    Защита: X-Webhook-Secret header.
    Идемпотентность: SQLite webhook_events table.
    """
    try:
        # Проверка секрета
        secret = settings.YOOKASSA_WEBHOOK_SECRET
        if secret and str(secret).strip():
            if not x_webhook_secret or x_webhook_secret != str(secret).strip():
                logger.warning("Webhook rejected: X-Webhook-Secret mismatch")
                raise HTTPException(status_code=401, detail="Unauthorized")
        else:
            logger.warning("YOOKASSA_WEBHOOK_SECRET not configured — accepting all requests (UNSAFE)")

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
            result = await _process_yookassa_payment(data)
            logger.info(f"Webhook {payment_id}: result={result}")
            return JSONResponse(status_code=200, content={"status": "ok", **result})

        elif event == "payment.canceled":
            logger.info(f"Webhook {payment_id}: payment canceled")
            # Логируем для аудита
            if settings.BOT_MODE != "legacy":
                from app.nodb.store import record_webhook_event, log_event
                await record_webhook_event(
                    external_id=payment_id,
                    event_type=event,
                    status="canceled",
                    processed=True,
                )
                await log_event(
                    event_type="yookassa_payment_canceled",
                    req_id=f"yookassa_{payment_id}",
                    payload=data.get("object", {}),
                )
            return JSONResponse(status_code=200, content={"status": "ok", "event": "canceled"})

        elif event == "refund.succeeded":
            logger.info(f"Webhook {payment_id}: refund succeeded")
            # Логируем для аудита
            if settings.BOT_MODE != "legacy":
                from app.nodb.store import log_event
                await log_event(
                    event_type="yookassa_refund",
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
        # Важно: возвращаем 200, чтобы YooKassa не ретраила бесконечно
        # Ошибка залогирована, можно разобраться вручную
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": str(e)[:100]}
        )
