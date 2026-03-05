"""
DEPRECATED: Legacy payment flow (YooKassa webhook, БД).
Используется только при BOT_MODE=legacy. Рекомендуется переход на BOT_MODE=no_db.
"""
import asyncio
import json
import uuid
from aiohttp import web
from aiogram import Router, types, F, Bot
from app.logger import logger

logger.warning("legacy payment flow is deprecated — consider BOT_MODE=no_db")

from app.services.payments.yookassa import create_payment, process_payment_webhook
from app.services.payments.recovery import recheck_single_payment
from app.services.cache import check_payment_rate_limit, try_schedule_autorecheck, get_redis_client
from app.keyboards import (
    get_subscription_link_keyboard,
    get_subscription_info_keyboard,
    get_main_menu_keyboard,
    get_back_to_plans_keyboard,
    get_payment_keyboard,
    get_new_payment_keyboard,
)
from app.services.users import get_user_active_subscription
from app.db.session import SessionLocal
from app.db.models import Subscription, Payment as PaymentModel
from sqlalchemy import select

router = Router(name="legacy_payments")

@router.callback_query(F.data.startswith("pay_yookassa_"))
async def handle_yookassa_payment(callback: types.CallbackQuery):
    """Обработчик выбора оплаты через Yookassa"""
    # Мгновенный фидбек через callback.answer()
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer("⏳ Создаю платеж...")
    
    try:
        # Парсим callback_data: pay_yookassa_{plan_code}_{period_months}_{amount}
        parts = callback.data.replace("pay_yookassa_", "").split("_")
        
        if len(parts) == 3:
            plan_code, period_months, amount_rub = parts
            period_months = int(period_months)
            amount_rub = int(amount_rub)
        elif len(parts) == 1:
            # Старый формат для обратной совместимости
            plan_code = parts[0]
            if plan_code == "basic":
                amount_rub = 99
                period_months = 1
            elif plan_code == "premium":
                amount_rub = 199
                period_months = 1
            else:
                # UI EXCEPTION: прямой вызов UI метода
                await callback.message.edit_text(
                    "❌ Неизвестный тариф",
                    reply_markup=get_back_to_plans_keyboard()
                )
                return
        else:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text(
                "❌ Неверный формат данных",
                reply_markup=get_back_to_plans_keyboard()
            )
            return
        
        # Определяем название тарифа
        if plan_code == "basic":
            plan_name = "Базовый тариф"
        elif plan_code == "premium":
            plan_name = "Премиум тариф"
        else:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text(
                "❌ Неизвестный тариф",
                reply_markup=get_back_to_plans_keyboard()
            )
            return
        
        period_text = f"{period_months} месяц" if period_months == 1 else f"{period_months} месяцев"
        
        payment_url, external_id = await create_payment(
            amount_rub=amount_rub,
            description=f"CRS VPN - {plan_name} ({period_text})",
            user_id=callback.from_user.id,
            plan_code=plan_code,
            period_months=period_months,
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
        await callback.answer("⚠️ Ссылка устарела. Создайте новый платёж.", show_alert=True)
        await callback.message.edit_text(
            "ℹ️ Ссылка на платёж устарела.\n\nСоздайте новый платёж через «Подписка» → «Выбрать тариф».",
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
                "ℹ️ Платёж не найден.\n\n"
                "Возможно, ссылка устарела — создайте новый платёж.",
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
                "ℹ️ <b>Платёж не найден</b>.\n\n"
                "Возможно, ссылка устарела — создайте новый платёж.",
                reply_markup=get_new_payment_keyboard(),
                parse_mode="HTML"
            )
            return

        if recheck_result.get("error") == "api_error":
            await callback.message.edit_text(
                "⚠️ Не удалось проверить статус в платёжной системе.\n\n"
                "Попробуйте позже или обратитесь в поддержку.",
                reply_markup=get_back_to_plans_keyboard()
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
                "⏳ Платёж ещё не получен.\n\n"
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
    """Обработчик кнопки получения ссылки подписки"""
    logger.info(f"Пользователь {callback.from_user.id} запросил ссылку подписки")
    # Даем мгновенный фидбек через callback.answer()
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer("⏳ Получаем ссылку подписки...")
    
    try:
        # Используем кэш для быстрого ответа
        subscription = await get_user_active_subscription(callback.from_user.id, use_cache=True)
        
        if not subscription:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text(
                "❌ <b>Подписка не найдена</b>\n\n"
                "У вас нет активной подписки. Пожалуйста, приобретите подписку.",
                reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id)
            )
            return
        
        subscription_url = None
        
        # Сначала проверяем сохраненную ссылку
        if subscription.config_data and "subscription_url" in subscription.config_data:
            subscription_url = subscription.config_data["subscription_url"]
            # Проверяем, что ссылка не пустая
            if subscription_url and subscription_url.strip():
                logger.info(f"✅ Использована сохраненная ссылка подписки для пользователя {callback.from_user.id}: {subscription_url[:50]}...")
            else:
                logger.warning(f"⚠️ Сохраненная ссылка пустая для пользователя {callback.from_user.id}")
                subscription_url = None
        
        # Если ссылки нет, пытаемся получить её из Remna API
        if not subscription_url:
            remna_user_id = subscription.remna_user_id
            if not remna_user_id:
                # Пробуем найти пользователя в Remna API по telegram_id
                logger.info(f"📥 remna_user_id не найден, поиск пользователя в Remna API по telegram_id={callback.from_user.id}...")
                from app.remnawave.client import RemnaClient
                client = RemnaClient()
                try:
                    # Ищем пользователя по telegramId
                    users_response = await client.get_users(size=100, start=1)
                    response_data = users_response.get('response', {})
                    users = response_data.get('users', [])
                    
                    found_user = None
                    for remna_user in users:
                        if remna_user.get('telegramId') == callback.from_user.id:
                            found_user = remna_user
                            break
                    
                    # Если не нашли на первой странице, ищем дальше
                    if not found_user:
                        page_size = 50
                        start = 51
                        while True:
                            users_response = await client.get_users(size=page_size, start=start)
                            response_data = users_response.get('response', {})
                            users = response_data.get('users', [])
                            
                            if not users:
                                break
                            
                            for remna_user in users:
                                if remna_user.get('telegramId') == callback.from_user.id:
                                    found_user = remna_user
                                    break
                            
                            if found_user or len(users) < page_size:
                                break
                            
                            start += page_size
                    
                    if found_user:
                        remna_user_id = found_user.get('uuid')
                        logger.info(f"✅ Найден пользователь в Remna API: uuid={remna_user_id}")
                        
                        # Сохраняем remna_user_id в БД
                        if SessionLocal:
                            async with SessionLocal() as session:
                                sub_result = await session.execute(
                                    select(Subscription).where(Subscription.id == subscription.id)
                                )
                                sub = sub_result.scalar_one_or_none()
                                if sub:
                                    sub.remna_user_id = str(remna_user_id)
                                    from app.db.models import TelegramUser
                                    user_result = await session.execute(
                                        select(TelegramUser).where(TelegramUser.telegram_id == callback.from_user.id)
                                    )
                                    user = user_result.scalar_one_or_none()
                                    if user:
                                        user.remna_user_id = str(remna_user_id)
                                    await session.commit()
                                    logger.info(f"✅ remna_user_id сохранен в БД")
                except Exception as e:
                    logger.error(f"❌ Ошибка при поиске пользователя в Remna API: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                finally:
                    await client.close()
            
            if remna_user_id:
                logger.info(f"📥 Получение ссылки подписки из Remna API для remna_user_id={remna_user_id}")
                from app.remnawave.client import RemnaClient
                client = RemnaClient()
                try:
                    subscription_url = await client.get_user_subscription_url(str(remna_user_id))
                    logger.info(f"📋 Результат получения ссылки: {subscription_url if subscription_url else 'None'}")
                    
                    if subscription_url and subscription_url.strip():
                        subscription_url = subscription_url.strip()  # Убираем пробелы
                        logger.info(f"✅ Subscription URL получен из Remna API: {subscription_url[:50]}...")
                        if SessionLocal:
                            async with SessionLocal() as session:
                                sub_result = await session.execute(
                                    select(Subscription).where(Subscription.id == subscription.id)
                                )
                                sub = sub_result.scalar_one_or_none()
                                if sub:
                                    if not sub.config_data:
                                        sub.config_data = {}
                                    sub.config_data["subscription_url"] = subscription_url
                                    await session.commit()
                                    logger.info(f"✅ Ссылка подписки сохранена в БД для пользователя {callback.from_user.id}: {subscription_url[:50]}...")
                    else:
                        logger.warning(f"⚠️ Не удалось получить ссылку подписки для remna_user_id={remna_user_id} (получено: {repr(subscription_url)})")
                        subscription_url = None  # Явно устанавливаем None
                except Exception as e:
                    logger.error(f"❌ Ошибка при получении ссылки подписки из Remna API: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                finally:
                    await client.close()
            else:
                # Если remna_user_id нет, пытаемся создать пользователя в Remna
                logger.info(f"📝 remna_user_id отсутствует, создание пользователя в Remna API для subscription_id={subscription.id}")
                from app.services.payments.yookassa import get_or_create_remna_user_and_get_subscription_url
                try:
                    subscription_url = await get_or_create_remna_user_and_get_subscription_url(
                        telegram_user_id=callback.from_user.id,
                        subscription_id=subscription.id
                    )
                    logger.info(f"📋 Результат создания пользователя и получения ссылки: {subscription_url if subscription_url else 'None'}")
                    if subscription_url and subscription_url.strip():
                        subscription_url = subscription_url.strip()
                        logger.info(f"✅ Пользователь создан в Remna и ссылка получена для пользователя {callback.from_user.id}: {subscription_url[:50]}...")
                    else:
                        logger.warning(f"⚠️ Ссылка не получена после создания пользователя: {repr(subscription_url)}")
                        subscription_url = None  # Явно устанавливаем None
                except Exception as e:
                    logger.error(f"❌ Ошибка при создании пользователя в Remna API: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
        
        # Финальная проверка и очистка subscription_url
        if subscription_url:
            subscription_url = subscription_url.strip()
        
        # Финальная проверка subscription_url перед отправкой
        logger.info(f"🔍 ФИНАЛЬНАЯ ПРОВЕРКА перед отправкой сообщения:")
        logger.info(f"   subscription_url: {repr(subscription_url)}")
        logger.info(f"   subscription_url is not None: {subscription_url is not None}")
        logger.info(f"   subscription_url.strip() if exists: {subscription_url.strip() if subscription_url else 'N/A'}")
        
        if subscription_url and subscription_url.strip():
            logger.info(f"✅ Финальная проверка: subscription_url валидна для пользователя {callback.from_user.id}: {subscription_url[:50]}...")
            logger.info(f"📤 Отправка сообщения со ссылкой подписки...")
        else:
            logger.error(f"❌ Финальная проверка: subscription_url невалидна для пользователя {callback.from_user.id} (значение: {repr(subscription_url)})")
            logger.error(f"   subscription.remna_user_id: {subscription.remna_user_id}")
            logger.error(f"   subscription.config_data: {subscription.config_data}")
            # Пробуем еще раз получить из БД
            if SessionLocal:
                try:
                    async with SessionLocal() as session:
                        sub_result = await session.execute(
                            select(Subscription).where(Subscription.id == subscription.id)
                        )
                        sub = sub_result.scalar_one_or_none()
                        if sub and sub.config_data and "subscription_url" in sub.config_data:
                            subscription_url = sub.config_data["subscription_url"]
                            if subscription_url and subscription_url.strip():
                                subscription_url = subscription_url.strip()
                                logger.info(f"✅ Subscription URL восстановлен из БД: {subscription_url[:50]}...")
                except Exception as e:
                    logger.error(f"❌ Ошибка при восстановлении из БД: {e}")
        
        if subscription_url and subscription_url.strip():
            from app.utils.html import escape_html
            message_text = (
                "🚀 <b>Ссылка для подключения VPN</b>\n\n"
                "Используйте эту ссылку для настройки VPN на вашем устройстве:\n\n"
                f"<code>{escape_html(subscription_url)}</code>\n\n"
                "💡 <b>Как использовать:</b>\n\n"
                "<b>Вариант 1:</b>\n"
                "<blockquote>\n"
                "1. Откройте ссылку\n"
                "2. Скачайте подходящий VPN клиент\n"
                "3. Импортируйте подписку\n"
                "</blockquote>\n\n"
                "<b>Вариант 2:</b>\n"
                "<blockquote>\n"
                "1. Скопируйте ссылку подписки\n"
                "2. Вставьте ее в VPN клиент\n"
                "</blockquote>"
            )
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            from app.ui.callbacks import build_cb
            # UI EXCEPTION: импорт ScreenID для передачи в ScreenManager
            from app.ui.screens import ScreenID
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Открыть ссылку", url=subscription_url)],
                [InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data=build_cb(ScreenID.CONNECT, "back")
                )]
            ])
            
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text(
                message_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text(
                "⚠️ <b>Ссылка недоступна</b>\n\n"
                "Не удалось получить ссылку подписки. Пожалуйста, попробуйте позже или обратитесь в поддержку.",
                reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id)
            )
            
    except Exception as e:
        logger.error(f"Ошибка при получении ссылки подписки: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        # UI EXCEPTION: прямой вызов UI метода
        await callback.message.edit_text(
            "❌ <b>Ошибка</b>\n\n"
            "Произошла ошибка при получении ссылки. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id)
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