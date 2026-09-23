"""
Legacy payment flow (YooKassa webhook, БД).
"""
import asyncio
import json
import uuid
from aiohttp import web
from aiogram import Router, types, F, Bot
from app.logger import logger

from app.services.payments.yookassa import create_payment, process_payment_webhook
from app.services.payments.recovery import recheck_single_payment
from app.services.cache import check_payment_rate_limit, try_schedule_autorecheck, get_redis_client
from app.keyboards import (
    get_subscription_info_keyboard,
    get_back_to_plans_keyboard,
    get_payment_keyboard,
    get_new_payment_keyboard,
)
from app.db.session import SessionLocal
from app.db.models import Payment as PaymentModel
from sqlalchemy import select

router = Router(name="legacy_payments")


def parse_pay_callback(data: str):
    """Разбирает pay_yookassa_* callback в (plan_code, period_months) или None.

    Форматы:
      pay_yookassa_{plan}_{months}           — текущий (с хотфикса 2.1);
      pay_yookassa_{plan}_{months}_{amount}  — старый, сумма ИГНОРИРУЕТСЯ;
      pay_yookassa_{plan}                    — самый старый (basic/premium), 1 месяц.
    """
    if not data or not data.startswith("pay_yookassa_"):
        return None
    parts = data[len("pay_yookassa_"):].split("_")
    plan_code = (parts[0] or "").lower().strip()
    if not plan_code:
        return None
    if len(parts) == 1:
        return plan_code, 1
    if len(parts) in (2, 3):
        try:
            months = int(parts[1])
        except (TypeError, ValueError):
            return None
        if months <= 0:
            return None
        return plan_code, months
    return None


# Правило цены живет в services/checkout (реэкспорт для старых импортов).
from app.services.checkout import resolve_purchase_amount  # noqa: E402,F401


@router.callback_query(F.data.startswith("pay_yookassa_"))
async def handle_yookassa_payment(callback: types.CallbackQuery):
    """Обработчик выбора оплаты через Yookassa"""
    # Мгновенный фидбек через callback.answer()
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer("⏳ Создаю платеж...")
    
    try:
        # Цена НИКОГДА не берется из callback_data (хотфикс 2.1). callback несет
        # только plan+months; сумма считается по каталогу core/plans.py.
        parsed = parse_pay_callback(callback.data)
        if parsed is None:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text(
                "❌ Неверный формат данных",
                reply_markup=get_back_to_plans_keyboard()
            )
            return
        plan_code, period_months = parsed

        from app.core.plans import get_plan_name
        amount_rub = await resolve_purchase_amount(plan_code, period_months, callback.from_user.id)
        if amount_rub <= 0:
            logger.warning(
                f"pay_yookassa rejected: user={callback.from_user.id} data={callback.data!r} "
                f"plan={plan_code} months={period_months}"
            )
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text(
                "❌ Этот тариф сейчас недоступен для покупки. Выберите тариф из меню.",
                reply_markup=get_back_to_plans_keyboard()
            )
            return
        plan_name = get_plan_name(plan_code)
        
        period_text = f"{period_months} месяц" if period_months == 1 else f"{period_months} месяцев"
        
        # Сумму create_payment считает сам (тот же resolve_purchase_amount);
        # amount_rub тут только для показа и как сверка.
        payment_url, external_id = await create_payment(
            amount_rub=amount_rub,
            description=f"CRS VPN - {plan_name} ({period_text})",
            user_id=callback.from_user.id,
            plan_code=plan_code,
            period_months=period_months,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
        )
        
        # Обновляем сообщение с результатом (без мусорных loading сообщений)
        # UI EXCEPTION: прямой вызов UI метода
        await callback.message.edit_text(
            f"💳 <b>{plan_name} - {period_text}</b>\n"
            f"💰 <b>Сумма:</b> {amount_rub}₽\n\n"
            "🔗 <b>Для оплаты перейдите по ссылке:</b>\n"
            f"<a href='{payment_url}'>Оплатить подписку</a>\n\n"
            "💡 После оплаты вы получите конфигурацию VPN",
            reply_markup=get_payment_keyboard(payment_url, external_id)
        )

        # Auto-recheck: best-effort, t+30s и t+90s. Не гарантируется при рестарте.
        # Основная страховка — кнопка «Проверить» + batch recovery (recheck_pending_payments).
        # Защита от дублирования: Redis autorecheck_scheduled:{external_id}, TTL=180s
        can_schedule = await try_schedule_autorecheck(external_id)
        if not can_schedule:
            logger.debug(f"autorecheck already scheduled: external_id={external_id}")
        else:
            if not get_redis_client():
                logger.warning("autorecheck: Redis unavailable, scheduling without dedup guard")
            async def _recheck_at(delay: int):
                await asyncio.sleep(delay)
                try:
                    await recheck_single_payment(
                        external_id=external_id,
                        bot=callback.bot,
                        trace_id=f"auto-recheck-{delay}s",
                    )
                except Exception as e:
                    logger.debug(f"auto-recheck {delay}s external_id={external_id}: {e}")

            asyncio.create_task(_recheck_at(30))
            asyncio.create_task(_recheck_at(90))
        
    except ValueError as e:
        error_msg = str(e)
        logger.error(f"Ошибка создания платежа Yookassa: {e}")
        
        # Более понятные сообщения для пользователя
        if "не настроен" in error_msg.lower() or "должен быть настроен" in error_msg.lower():
            user_message = (
                "❌ <b>Платежи не настроены</b>\n\n"
                "Платежная система не настроена. Обратитесь к администратору: @dcfrq"
            )
        elif "авторизации" in error_msg.lower() or "api ключ" in error_msg.lower():
            user_message = (
                "❌ <b>Ошибка настройки платежей</b>\n\n"
                "Проблема с настройкой платежной системы. Обратитесь к администратору: @dcfrq"
            )
        else:
            user_message = (
                "❌ <b>Ошибка создания платежа</b>\n\n"
                f"Произошла ошибка: {error_msg}\n\n"
                "Попробуйте позже или обратитесь в поддержку."
            )
        
        # UI EXCEPTION: прямой вызов UI метода
        await callback.message.edit_text(
            user_message,
            reply_markup=get_back_to_plans_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка создания платежа Yookassa: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        # UI EXCEPTION: прямой вызов UI метода
        await callback.message.edit_text(
            "❌ <b>Ошибка создания платежа</b>\n\n"
            "Произошла ошибка при создании платежа. Попробуйте позже или обратитесь в поддержку.",
            reply_markup=get_back_to_plans_keyboard()
        )


@router.callback_query(F.data.startswith("check_payment"))
async def handle_check_payment(callback: types.CallbackQuery):
    """Обработчик кнопки 'Проверить оплату' — синхронизирует статус с YooKassa по external_id"""
    trace_id = str(uuid.uuid4())
    user_id = callback.from_user.id

    # Парсим external_id из callback_data: check_payment:<external_id>
    parts = callback.data.split(":", 1)
    external_id = parts[1] if len(parts) > 1 and parts[1] else None

    if not external_id:
        await callback.answer("⚠️ Ссылка устарела. Создайте новый платеж.", show_alert=True)
        await callback.message.edit_text(
            "ℹ️ Ссылка на платеж устарела.\n\nСоздайте новый платеж через «Подписка» → «Выбрать тариф».",
            reply_markup=get_back_to_plans_keyboard()
        )
        return

    # Anti-spam: rate limit 1 раз в 10 секунд (Redis)
    allowed, seconds_left = await check_payment_rate_limit(user_id, external_id)
    if not allowed:
        await callback.answer(f"⏳ Подожди {seconds_left} сек. перед повторной проверкой.", show_alert=True)
        return

    await callback.answer("⏳ Проверяю статус оплаты...")

    if not SessionLocal:
        await callback.message.edit_text(
            "❌ Сервис временно недоступен. Попробуйте позже.",
            reply_markup=get_back_to_plans_keyboard()
        )
        return

    try:
        async with SessionLocal() as session:
            result = await session.execute(
                select(PaymentModel)
                .where(
                    PaymentModel.external_id == external_id,
                    PaymentModel.telegram_user_id == user_id,
                    PaymentModel.provider == "yookassa",
                )
            )
            payment = result.scalar_one_or_none()

        if not payment:
            logger.warning(f"[{trace_id}] check_payment: payment not found external_id={external_id} tg_user_id={user_id}")
            await callback.message.edit_text(
                "ℹ️ Платеж не найден.\n\n"
                "Возможно, ссылка устарела — создайте новый платеж.",
                reply_markup=get_new_payment_keyboard()
            )
            return

        recheck_result = await recheck_single_payment(
            external_id=payment.external_id,
            bot=callback.bot,
            trace_id=trace_id,
        )

        logger.info(
            f"[{trace_id}] check_payment: user={user_id} external_id={payment.external_id} "
            f"updated={recheck_result.get('updated')} status={recheck_result.get('status')} "
            f"provisioned={recheck_result.get('provisioned')}"
        )

        if recheck_result.get("error") == "not_found":
            logger.info(f"[{trace_id}] check_payment NOT_FOUND: tg_user_id={user_id} external_id={external_id}")
            await callback.message.edit_text(
                "ℹ️ <b>Платеж не найден</b>.\n\n"
                "Возможно, ссылка устарела — создайте новый платеж.",
                reply_markup=get_new_payment_keyboard(),
                parse_mode="HTML"
            )
            return

        if recheck_result.get("error") == "api_error":
            await callback.message.edit_text(
                "⚠️ Не удалось проверить статус в платежной системе.\n\n"
                "Попробуйте позже или обратитесь в поддержку.",
                reply_markup=get_back_to_plans_keyboard()
            )
            return

        if recheck_result.get("status") == "review":
            await callback.message.edit_text(
                "⏳ <b>Оплата получена</b>\n\n"
                "Платеж на ручной проверке у администратора. Мы свяжемся с вами.",
                reply_markup=get_back_to_plans_keyboard(),
                parse_mode="HTML"
            )
            return

        if recheck_result.get("provisioned"):
            await callback.message.edit_text(
                "✅ <b>Оплата подтверждена!</b>\n\n"
                "Подписка активирована. Нажмите «Получить ссылку» для настройки VPN.",
                reply_markup=get_subscription_info_keyboard(has_subscription=True),
                parse_mode="HTML"
            )
            return

        if recheck_result.get("status") == "succeeded":
            await callback.message.edit_text(
                "✅ Оплата подтверждена. Подписка должна быть уже активирована.",
                reply_markup=get_subscription_info_keyboard(has_subscription=True)
            )
            return

        if recheck_result.get("status") == "pending":
            payment_url = ""
            if callback.message.reply_markup and callback.message.reply_markup.inline_keyboard:
                for row in callback.message.reply_markup.inline_keyboard:
                    for btn in row:
                        if getattr(btn, "url", None):
                            payment_url = btn.url
                            break
                    if payment_url:
                        break
            await callback.message.edit_text(
                "⏳ Платеж еще не получен.\n\n"
                "Если вы уже оплатили — подождите 1–2 минуты и нажмите «Проверить оплату» снова.",
                reply_markup=get_payment_keyboard(payment_url, external_id) if payment_url else get_back_to_plans_keyboard()
            )
            return

        await callback.message.edit_text(
            f"ℹ️ Статус платежа: {recheck_result.get('status', 'unknown')}.\n\n"
            "Если есть вопросы — обратитесь в поддержку.",
            reply_markup=get_back_to_plans_keyboard()
        )

    except Exception as e:
        logger.error(f"[{trace_id}] check_payment error: user={user_id} err={e}")
        await callback.message.edit_text(
            "❌ Ошибка при проверке. Попробуйте позже.",
            reply_markup=get_back_to_plans_keyboard()
        )


@router.callback_query(lambda c: c.data == "get_subscription_link")
async def get_subscription_link(callback: types.CallbackQuery):
    """Алиас старой кнопки «🔗 Получить ссылку» (хотфикс 2.1).

    Раньше здесь был отдельный legacy-экран: только основная ссылка (Pro не
    видел ссылку обхода) и полный перебор юзеров Remnawave. Теперь ведет на тот
    же экран «Подключиться», что и connect_vpn. Новые сообщения уже используют
    callback connect_vpn; алиас нужен для кнопок в старых сообщениях.
    """
    logger.info(f"Пользователь {callback.from_user.id} запросил ссылку подписки (алиас → connect)")
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer("⏳ Получаем ссылку подписки...")

    from app.ui.screen_manager import get_screen_manager
    from app.ui.screens import ScreenID

    await get_screen_manager().handle_action(
        screen_id=ScreenID.CONNECT,
        action="open",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id,
    )


async def yookassa_webhook_handler(request: web.Request) -> web.Response:
    """Обработчик webhook от YooKassa"""
    try:
        # Проверяем метод запроса
        if request.method != "POST":
            logger.warning(f"Получен запрос с неправильным методом: {request.method}")
            return web.Response(status=405, text="Method not allowed")

        # Секрет проверяется только в FastAPI (порт 8001). Этот handler — aiogram, не используется для YooKassa в PROD.
        
        # Получаем данные
        try:
            data = await request.json()
        except Exception as e:
            logger.error(f"Ошибка при парсинге JSON webhook: {e}")
            return web.Response(status=400, text="Invalid JSON")
        
        if not data:
            logger.error("Получен пустой webhook")
            return web.Response(status=400, text="Empty request body")
        
        event = data.get('event', 'unknown')
        logger.info(f"Получен webhook от YooKassa: {event}")
        
        # Получаем бота из приложения
        bot: Bot = request.app.get("bot")
        if not bot:
            logger.error("Бот не найден в приложении")
            return web.Response(status=500, text="Bot not found")
        
        # Обрабатываем webhook
        success = await process_payment_webhook(data, bot)
        
        if success:
            logger.info(f"Webhook успешно обработан: {event}")
            return web.Response(status=200, text="OK")
        else:
            logger.warning(f"Ошибка при обработке webhook: {event}")
            return web.Response(status=400, text="Error processing webhook")
            
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка декодирования JSON в webhook: {e}")
        return web.Response(status=400, text="Invalid JSON format")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при обработке webhook YooKassa: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return web.Response(status=500, text="Internal server error")