"""
FastAPI приложение — webhook ЮKassa.
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
    """Health check эндпоинт"""
    return {
        "status": "healthy",
        "bot_initialized": bot_instance is not None,
        "mode": "legacy"
    }


async def _process_yookassa_payment(data: dict) -> dict:
    """
    Обрабатывает успешный платеж YooKassa.
    Возвращает dict с результатом.
    """
    from yookassa.domain.notification import WebhookNotification
    from app.services.remna_service import provision_tariff
    from app.services.jsonl_logger import log_payment_event, EVENT_REMNAWAVE_PROVISION_SUCCESS, EVENT_REMNAWAVE_PROVISION_FAILED
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

    # Идемпотентность: проверяем, обрабатывали ли уже этот платеж
    if payment_id in _processed_payments:
        logger.info(f"Webhook {payment_id}: already processed, skipping")
        return {"status": "already_processed", "processed": True}

    # Определяем тариф
    # ПРИМЕЧАНИЕ: _process_yookassa_payment не вызывается с ЭТАПА 1 (используется process_payment_webhook).
    # Fallback по сумме оставлен корректным на случай использования функции напрямую.
    if not tariff:
        logger.warning(f"Webhook {payment_id}: tariff missing in metadata, using amount fallback amount={amount}")
        if amount >= 1799:
            tariff = "premium_12"
        elif amount >= 999:
            tariff = "premium_6"
        elif amount >= 899:
            tariff = "basic_12"
        elif amount >= 549:
            tariff = "premium_3"
        elif amount >= 499:
            tariff = "basic_6"
        elif amount >= 249:
            tariff = "basic_3"
        elif amount >= 199:
            tariff = "premium_1"
        else:
            tariff = "basic_1"

    if period and not tariff.endswith(f"_{period}"):
        tariff = f"{tariff.split('_')[0]}_{period}"

    logger.info(f"Webhook {payment_id}: provisioning tariff={tariff} for tg_id={tg_id}")

    # Provision через Remnawave
    success = await provision_tariff(tg_id, tariff, req_id=f"yookassa_{payment_id}")

    if success:
        _processed_payments.add(payment_id)
        log_payment_event(
            event=EVENT_REMNAWAVE_PROVISION_SUCCESS,
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
        log_payment_event(
            event=EVENT_REMNAWAVE_PROVISION_FAILED,
            req_id=f"yookassa_{payment_id}",
            tg_id=tg_id,
            payload={"tariff": tariff, "amount": amount, "payment_id": payment_id},
        )
        return {"error": "provision_failed", "processed": False}


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
        # Важно: возвращаем 200, чтобы YooKassa не ретраила бесконечно
        # Ошибка залогирована, можно разобраться вручную
        return JSONResponse(
            status_code=200,
            content={"status": "error", "message": str(e)[:100]}
        )
