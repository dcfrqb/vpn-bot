from aiogram import Router, types
from aiogram.filters import CommandStart, Command
import asyncio
from app.logger import logger
from app.services.users import get_or_create_telegram_user, get_user_active_subscription
from app.keyboards import (
    get_main_menu_keyboard,
    get_plans_keyboard,
    get_payment_keyboard,
    get_back_to_plans_keyboard,
    get_help_keyboard,
    get_subscription_info_keyboard,
    get_payment_method_keyboard,
    get_period_keyboard
)
from app.config import is_admin
from datetime import datetime

router = Router(name="start")

@router.message(CommandStart())
async def cmd_start(m: types.Message):
    logger.info(f"Пользователь {m.from_user.id} (@{m.from_user.username}) запустил бота")
    
    # Получаем параметры из команды /start
    command_args = m.text.split()[1:] if len(m.text.split()) > 1 else []
    start_param = command_args[0] if command_args else None
    
    # Оптимизация: получаем пользователя и подписку параллельно
    user_task = None
    subscription_task = None
    
    try:
        # Запускаем получение пользователя
        user_task = asyncio.create_task(
            get_or_create_telegram_user(
                telegram_id=m.from_user.id,
                username=m.from_user.username,
                first_name=m.from_user.first_name,
                last_name=m.from_user.last_name,
                language_code=m.from_user.language_code,
                create_trial=False  # Убрали пробный период
            )
        )
        # Параллельно получаем подписку (если пользователь уже существует)
        subscription_task = asyncio.create_task(
            get_user_active_subscription(m.from_user.id, use_cache=True)
        )
    except Exception as e:
        logger.error(f"Ошибка при регистрации пользователя: {e}")
    
    # Обработка параметров из deep link
    if start_param == "payment_success":
        # Пользователь вернулся после оплаты через ЮКассу
        logger.info(f"Пользователь {m.from_user.id} вернулся после оплаты через ЮКассу")
        
        # Проверяем статус последнего платежа
        from app.db.session import SessionLocal
        from app.db.models import Payment as PaymentModel
        from sqlalchemy import select
        from app.services.payments.yookassa import check_payment_status, handle_successful_payment
        
        # Используем уже полученную подписку или получаем заново
        if subscription_task:
            try:
                subscription = await subscription_task
            except:
                subscription = await get_user_active_subscription(m.from_user.id, use_cache=True)
        else:
            subscription = await get_user_active_subscription(m.from_user.id, use_cache=True)
        
        # Ждем завершения получения пользователя
        if user_task:
            try:
                await user_task
            except:
                pass
        
        if subscription:
            # Подписка уже активирована
            await m.answer(
                "✅ <b>Спасибо за оплату!</b>\n\n"
                "Ваш платеж успешно обработан. Подписка активирована!\n\n"
                "Теперь вы можете получить ссылку для настройки VPN.",
                reply_markup=get_subscription_info_keyboard(has_subscription=True),
                parse_mode="HTML"
            )
        elif SessionLocal:
            # Проверяем последний платеж пользователя
            try:
                async with SessionLocal() as session:
                    result = await session.execute(
                        select(PaymentModel)
                        .where(
                            PaymentModel.telegram_user_id == m.from_user.id,
                            PaymentModel.provider == "yookassa"
                        )
                        .order_by(PaymentModel.created_at.desc())
                        .limit(1)
                    )
                    payment = result.scalar_one_or_none()
                    
                    if payment:
                        # Проверяем статус платежа в ЮКассе, если он еще pending
                        if payment.status == "pending":
                            payment_status = await check_payment_status(payment.external_id)
                            if payment_status and payment_status.get("status") == "succeeded":
                                # Платеж успешен, обрабатываем его
                                await handle_successful_payment(
                                    session=session,
                                    payment_id=payment.id,
                                    telegram_user_id=m.from_user.id,
                                    amount=payment_status.get("amount", payment.amount),
                                    description=payment.description or "CRS VPN 30 дней",
                                    bot=m.bot
                                )
                                await m.answer(
                                    "✅ <b>Платеж успешно обработан!</b>\n\n"
                                    "Ваша подписка активирована. Теперь вы можете получить ссылку для настройки VPN.",
                                    reply_markup=get_subscription_info_keyboard(has_subscription=True),
                                    parse_mode="HTML"
                                )
                                logger.info(f"Платеж {payment.external_id} обработан после возврата пользователя")
                            else:
                                await m.answer(
                                    "⏳ <b>Обработка платежа</b>\n\n"
                                    "Ваш платеж получен и обрабатывается. "
                                    "Вы получите уведомление, как только подписка будет активирована.",
                                    reply_markup=get_main_menu_keyboard(user_id=m.from_user.id),
                                    parse_mode="HTML"
                                )
                        elif payment.status == "succeeded":
                            # Платеж уже успешен, но подписка не активирована - обрабатываем
                            if not payment.subscription_id:
                                await handle_successful_payment(
                                    session=session,
                                    payment_id=payment.id,
                                    telegram_user_id=m.from_user.id,
                                    amount=float(payment.amount),
                                    description=payment.description or "CRS VPN 30 дней",
                                    bot=m.bot
                                )
                                await m.answer(
                                    "✅ <b>Платеж успешно обработан!</b>\n\n"
                                    "Ваша подписка активирована. Теперь вы можете получить ссылку для настройки VPN.",
                                    reply_markup=get_subscription_info_keyboard(has_subscription=True),
                                    parse_mode="HTML"
                                )
                            else:
                                await m.answer(
                                    "✅ <b>Спасибо за оплату!</b>\n\n"
                                    "Ваш платеж успешно обработан. Подписка активирована!\n\n"
                                    "Теперь вы можете получить ссылку для настройки VPN.",
                                    reply_markup=get_subscription_info_keyboard(has_subscription=True),
                                    parse_mode="HTML"
                                )
                        else:
                            await m.answer(
                                "⏳ <b>Обработка платежа</b>\n\n"
                                "Ваш платеж получен и обрабатывается. "
                                "Вы получите уведомление, как только подписка будет активирована.",
                                reply_markup=get_main_menu_keyboard(user_id=m.from_user.id),
                                parse_mode="HTML"
                            )
                    else:
                        await m.answer(
                            "⏳ <b>Обработка платежа</b>\n\n"
                            "Ваш платеж получен и обрабатывается. "
                            "Вы получите уведомление, как только подписка будет активирована.",
                            reply_markup=get_main_menu_keyboard(user_id=m.from_user.id),
                            parse_mode="HTML"
                        )
            except Exception as e:
                logger.error(f"Ошибка при обработке возврата после оплаты: {e}")
                await m.answer(
                    "⏳ <b>Обработка платежа</b>\n\n"
                    "Ваш платеж получен и обрабатывается. "
                    "Вы получите уведомление, как только подписка будет активирована.",
                    reply_markup=get_main_menu_keyboard(user_id=m.from_user.id),
                    parse_mode="HTML"
                )
        else:
            await m.answer(
                "⏳ <b>Обработка платежа</b>\n\n"
                "Ваш платеж получен и обрабатывается. "
                "Вы получите уведомление, как только подписка будет активирована.",
                reply_markup=get_main_menu_keyboard(user_id=m.from_user.id),
                parse_mode="HTML"
            )
    else:
        # Обычный запуск бота - показываем профиль и подписку
        from app.db.session import SessionLocal
        from app.db.models import TelegramUser
        from sqlalchemy import select
        
        # Используем уже полученную подписку или получаем заново
        if subscription_task:
            try:
                subscription = await subscription_task
            except:
                subscription = await get_user_active_subscription(m.from_user.id, use_cache=True)
        else:
            subscription = await get_user_active_subscription(m.from_user.id, use_cache=True)
        
        # Ждем завершения получения пользователя
        if user_task:
            try:
                await user_task
            except:
                pass
        
        # Получаем данные пользователя
        user_name = f"{m.from_user.first_name or ''} {m.from_user.last_name or ''}".strip()
        if not user_name:
            user_name = m.from_user.username or "Пользователь"
        
        # Формируем сообщение с профилем
        profile_text = "👤 Профиль:\n"
        profile_text += "• ID: {}\n".format(m.from_user.id)
        profile_text += "• Имя: {}".format(user_name)
        
        # Формируем сообщение с подпиской (используем единую функцию)
        from app.services.subscription_formatter import format_subscription_info
        subscription_text, has_subscription = format_subscription_info(subscription)
        
        # Объединяем текст
        welcome_text = f"{profile_text}\n\n{subscription_text}"
        
        await m.answer(
            welcome_text,
            reply_markup=get_main_menu_keyboard(user_id=m.from_user.id, has_subscription=has_subscription)
        )
    
    logger.info("Приветственное сообщение отправлено")

@router.callback_query(lambda c: c.data == "buy_subscription")
async def buy_subscription(callback: types.CallbackQuery):
    logger.info(f"Пользователь {callback.from_user.id} нажал 'Купить подписку'")
    await callback.answer()  # Быстрый ответ, без запросов к БД
    
    # Удаляем сообщения об оплате USDT в фоне (не блокируем)
    try:
        from app.routers.crypto_payments import delete_crypto_payment_messages
        asyncio.create_task(delete_crypto_payment_messages(callback.bot, None, callback.from_user.id))
    except:
        pass
    
    await callback.message.edit_text(
        "💳 <b>Покупка подписки</b>\n\n"
        "Выберите тариф:",
        reply_markup=get_plans_keyboard()
    )

@router.callback_query(lambda c: c.data == "my_plan")
async def my_plan(callback: types.CallbackQuery):
    # Отвечаем сразу, не ждем бизнес-логику
    logger.info(f"Пользователь {callback.from_user.id} нажал 'Мой тариф'")
    await callback.answer()  # Быстрый ответ без текста
    
    # Используем кэш для быстрого ответа
    subscription = await get_user_active_subscription(callback.from_user.id, use_cache=True)
    
    if subscription:
        if subscription.valid_until:
            valid_until_str = subscription.valid_until.strftime("%d.%m.%Y %H:%M")
            days_left = (subscription.valid_until - datetime.utcnow()).days
        else:
            valid_until_str = "Без ограничений"
            days_left = None
        
        plan_name = subscription.plan_name or subscription.plan_code.upper()
        
        text = (
            f"🧾 <b>Ваш тариф</b>\n\n"
            f"✅ <b>Активная подписка:</b> Да\n"
            f"📅 <b>Срок действия:</b> {valid_until_str}\n"
        )
        
        if days_left is not None:
            if days_left > 0:
                text += f"⏰ <b>Осталось дней:</b> {days_left}\n"
            else:
                text += f"⚠️ <b>Статус:</b> Истекает сегодня\n"
        
        # Добавляем информацию о пробном периоде
        if subscription.plan_code == "trial":
            text += (
                f"💳 <b>Тариф:</b> {plan_name}\n\n"
                f"🎁 <b>Пробный период активен!</b>\n"
                f"После окончания пробного периода выберите подходящий тариф для продолжения использования VPN."
            )
        else:
            text += (
                f"💳 <b>Тариф:</b> {plan_name}\n\n"
                f"🎉 Ваша подписка активна! Используйте VPN для безопасного интернета."
            )
        
        await callback.message.edit_text(
            text,
            reply_markup=get_subscription_info_keyboard(has_subscription=True)
        )
    else:
        # Используем кэш для быстрого ответа
        subscription = await get_user_active_subscription(callback.from_user.id, use_cache=True)
        has_sub = subscription is not None
        
        await callback.message.edit_text(
            "🧾 <b>Ваш тариф</b>\n\n"
            "❌ <b>Активная подписка:</b> Нет\n"
            "📅 <b>Срок действия:</b> Не установлен\n"
            "💳 <b>Тариф:</b> Не выбран\n\n"
            "💡 <b>Новые пользователи получают пробный период на 2 дня!</b>\n\n"
            "Для получения доступа к VPN выберите подходящий тариф:",
            reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=has_sub)
        )

@router.callback_query(lambda c: c.data == "connect_vpn")
async def connect_vpn(callback: types.CallbackQuery):
    """Обработчик кнопки 'Подключиться' - получение ссылки VPN"""
    logger.info(f"Пользователь {callback.from_user.id} нажал 'Подключиться'")
    await callback.answer()  # Отвечаем сразу
    
    # Используем логику из get_subscription_link
    from app.db.session import SessionLocal
    from app.db.models import Subscription
    from sqlalchemy import select
    from app.remnawave.client import RemnaClient
    from app.services.payments.yookassa import get_or_create_remna_user_and_get_subscription_url
    
    try:
        # Используем кэш для быстрого ответа
        subscription = await get_user_active_subscription(callback.from_user.id, use_cache=True)
        
        if not subscription:
            await callback.message.edit_text(
                "❌ <b>Подписка не найдена</b>\n\n"
                "У вас нет активной подписки. Пожалуйста, приобретите подписку.",
                reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=False)
            )
            return
        
        subscription_url = None
        
        # Сначала проверяем сохраненную ссылку
        if subscription.config_data and "subscription_url" in subscription.config_data:
            subscription_url = subscription.config_data["subscription_url"]
            if subscription_url and subscription_url.strip():
                logger.info(f"✅ Использована сохраненная ссылка подписки для пользователя {callback.from_user.id}")
            else:
                subscription_url = None
        
        # Если ссылки нет, получаем её из Remna API в фоне
        if not subscription_url:
            if subscription.remna_user_id:
                # Получаем ссылку в фоне, не блокируем ответ
                async def get_url_background():
                    try:
                        logger.info(f"📥 Фоновая задача: получение subscription URL для remna_user_id={subscription.remna_user_id}")
                        client = RemnaClient()
                        try:
                            url = await client.get_user_subscription_url(subscription.remna_user_id)
                            logger.info(f"📋 Фоновая задача: получен URL: {url[:50] if url else 'None'}...")
                            if url and url.strip() and SessionLocal:
                                url = url.strip()
                                # Сохраняем в БД
                                async with SessionLocal() as session:
                                    sub_result = await session.execute(
                                        select(Subscription).where(Subscription.id == subscription.id)
                                    )
                                    sub = sub_result.scalar_one_or_none()
                                    if sub:
                                        if not sub.config_data:
                                            sub.config_data = {}
                                        sub.config_data["subscription_url"] = url
                                        await session.commit()
                                
                                # Обновляем сообщение пользователю
                                logger.info(f"📤 Фоновая задача: отправка сообщения со ссылкой пользователю {callback.from_user.id}")
                                try:
                                    await callback.bot.edit_message_text(
                                        chat_id=callback.message.chat.id,
                                        message_id=callback.message.message_id,
                                        text=(
                                            "🚀 <b>Ссылка для подключения VPN</b>\n\n"
                                            "Используйте эту ссылку для настройки VPN на вашем устройстве:\n\n"
                                            f"<code>{url}</code>\n\n"
                                            "💡 <b>Как использовать:</b>\n\n"
                                            "<b>Вариант 1:</b>\n"
                                            "1. Откройте ссылку\n"
                                            "2. Скачайте подходящий VPN клиент\n"
                                            "3. Импортируйте подписку\n\n"
                                            "<b>Вариант 2:</b>\n"
                                            "1. Скопируйте ссылку подписки\n"
                                            "2. Вставьте ее в VPN клиент"
                                        ),
                                        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                                            [types.InlineKeyboardButton(text="🔗 Открыть ссылку", url=url)],
                                            [types.InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")]
                                        ]),
                                        parse_mode="HTML"
                                    )
                                    logger.info(f"✅ Фоновая задача: сообщение успешно отправлено пользователю {callback.from_user.id}")
                                except Exception as edit_e:
                                    logger.error(f"❌ Фоновая задача: ошибка при отправке сообщения: {edit_e}")
                                    import traceback
                                    logger.error(traceback.format_exc())
                            else:
                                await callback.bot.edit_message_text(
                                    chat_id=callback.message.chat.id,
                                    message_id=callback.message.message_id,
                                    text=(
                                        "❌ <b>Не удалось получить ссылку</b>\n\n"
                                        "Пожалуйста, попробуйте позже или обратитесь в поддержку: @dcfrq"
                                    ),
                                    reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=True)
                                )
                        except Exception as e:
                            logger.error(f"❌ Ошибка при получении ссылки подписки из Remna API: {e}")
                            await callback.bot.edit_message_text(
                                chat_id=callback.message.chat.id,
                                message_id=callback.message.message_id,
                                text=(
                                    "❌ <b>Ошибка при получении ссылки</b>\n\n"
                                    "Пожалуйста, попробуйте позже или обратитесь в поддержку: @dcfrq"
                                ),
                                reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=True)
                            )
                        finally:
                            await client.close()
                    except Exception as e:
                        logger.error(f"Ошибка в фоновой задаче получения ссылки: {e}")
                
                # Запускаем в фоне
                asyncio.create_task(get_url_background())
                return  # Выходим, фоновый таск обновит сообщение
            else:
                # Если remna_user_id нет, создаем пользователя в Remna (может быть долго)
                # Выполняем в фоне и показываем уведомление
                async def create_remna_user_background():
                    try:
                        logger.info(f"📥 Фоновая задача: создание пользователя в Remna для telegram_user_id={callback.from_user.id}")
                        url = await get_or_create_remna_user_and_get_subscription_url(
                            telegram_user_id=callback.from_user.id,
                            subscription_id=subscription.id
                        )
                        logger.info(f"📋 Фоновая задача: получен URL после создания пользователя: {url[:50] if url else 'None'}...")
                        if url:
                            try:
                                await callback.bot.edit_message_text(
                                    chat_id=callback.message.chat.id,
                                    message_id=callback.message.message_id,
                                    text=(
                                        "🚀 <b>Ссылка для подключения VPN</b>\n\n"
                                        "Используйте эту ссылку для настройки VPN на вашем устройстве:\n\n"
                                        f"<code>{url}</code>\n\n"
                                        "💡 <b>Как использовать:</b>\n\n"
                                        "<b>Вариант 1:</b>\n"
                                        "1. Откройте ссылку\n"
                                        "2. Скачайте подходящий VPN клиент\n"
                                        "3. Импортируйте подписку\n\n"
                                        "<b>Вариант 2:</b>\n"
                                        "1. Скопируйте ссылку подписки\n"
                                        "2. Вставьте ее в VPN клиент"
                                    ),
                                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                                        [types.InlineKeyboardButton(text="🔗 Открыть ссылку", url=url)],
                                        [types.InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")]
                                    ]),
                                    parse_mode="HTML"
                                )
                                logger.info(f"✅ Фоновая задача: сообщение успешно отправлено пользователю {callback.from_user.id}")
                            except Exception as edit_e:
                                logger.error(f"❌ Фоновая задача: ошибка при отправке сообщения: {edit_e}")
                                import traceback
                                logger.error(traceback.format_exc())
                        else:
                            await callback.bot.edit_message_text(
                                chat_id=callback.message.chat.id,
                                message_id=callback.message.message_id,
                                text=(
                                    "❌ <b>Не удалось получить ссылку</b>\n\n"
                                    "Пожалуйста, попробуйте позже или обратитесь в поддержку: @dcfrq"
                                ),
                                reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=True)
                            )
                    except Exception as e:
                        logger.error(f"Ошибка создания пользователя Remna: {e}")
                        await callback.bot.edit_message_text(
                            chat_id=callback.message.chat.id,
                            message_id=callback.message.message_id,
                            text=(
                                "❌ <b>Произошла ошибка</b>\n\n"
                                "Пожалуйста, попробуйте позже или обратитесь в поддержку: @dcfrq"
                            ),
                            reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=True)
                        )
                
                # Запускаем в фоне
                asyncio.create_task(create_remna_user_background())
                return  # Выходим, фоновый таск обновит сообщение
        
        if subscription_url and subscription_url.strip():
            message_text = (
                "🚀 <b>Ссылка для подключения VPN</b>\n\n"
                "Используйте эту ссылку для настройки VPN на вашем устройстве:\n\n"
                f"<code>{subscription_url}</code>\n\n"
                "💡 <b>Как использовать:</b>\n\n"
                "<b>Вариант 1:</b>\n"
                "1. Откройте ссылку\n"
                "2. Скачайте подходящий VPN клиент\n"
                "3. Импортируйте подписку\n\n"
                "<b>Вариант 2:</b>\n"
                "1. Скопируйте ссылку подписки\n"
                "2. Вставьте ее в VPN клиент"
            )
            
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Открыть ссылку", url=subscription_url)],
                [InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_main")]
            ])
            
            await callback.message.edit_text(
                message_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                "❌ <b>Не удалось получить ссылку</b>\n\n"
                "Пожалуйста, попробуйте позже или обратитесь в поддержку: @dcfrq",
                reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=True)
            )
    except Exception as e:
        logger.error(f"Ошибка при получении ссылки VPN: {e}")
        await callback.message.edit_text(
            "❌ <b>Произошла ошибка</b>\n\n"
            "Пожалуйста, попробуйте позже или обратитесь в поддержку: @dcfrq",
            reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=True)
        )

@router.callback_query(lambda c: c.data == "help")
async def help_info(callback: types.CallbackQuery):
    logger.info(f"Пользователь {callback.from_user.id} нажал 'Помощь'")
    await callback.answer()  # Быстрый ответ
    await callback.message.edit_text(
        "ℹ️ <b>Справка по CRS-VPN</b>\n\n"
        "🔐 <b>Что такое VPN?</b>\n"
        "VPN (Virtual Private Network) — это технология, которая создает защищенное соединение между вашим устройством и интернетом.\n\n"
        "✅ <b>Преимущества:</b>\n"
        "• Защита данных в общественных Wi-Fi\n"
        "• Обход географических ограничений\n"
        "• Анонимность в интернете\n\n"
        "📱 <b>Как пользоваться:</b>\n"
        "1. Выберите подходящий тариф\n"
        "2. Оплатите подписку\n"
        "3. Нажмите 'Подключиться' для получения ссылки\n"
        "4. Настройте VPN на своем устройстве\n\n"
        "📖 <b>Еще немного о VPN:</b>\n"
        "VPN позволяет шифровать весь ваш интернет-трафик, делая его недоступным для провайдеров, хакеров и других третьих лиц. "
        "Это особенно важно при использовании общественных Wi-Fi сетей, где ваши данные могут быть перехвачены.\n\n"
        "🆘 <b>Нужна помощь?</b>\n"
        "Обратитесь к администратору: @dcfrq",
        reply_markup=get_help_keyboard()
    )

@router.callback_query(lambda c: c.data == "plan_basic")
async def plan_basic(callback: types.CallbackQuery):
    logger.info(f"Пользователь {callback.from_user.id} выбрал тариф 'Базовый'")
    await callback.answer()
    
    await callback.message.edit_text(
        "💳 <b>Базовый тариф</b>\n\n"
        "📋 <b>Включено:</b>\n"
        "• Неограниченный трафик и скорость\n"
        "• Поддержка разных устройств\n"
        "• YouTube без рекламы\n"
        "• Сервера NL\n\n"
        "💡 Выберите период подписки:",
        reply_markup=get_period_keyboard("basic")
    )

@router.callback_query(lambda c: c.data == "plan_premium")
async def plan_premium(callback: types.CallbackQuery):
    logger.info(f"Пользователь {callback.from_user.id} выбрал тариф 'Премиум'")
    await callback.answer()
    
    await callback.message.edit_text(
        "💳 <b>Премиум тариф</b>\n\n"
        "📋 <b>Включено:</b>\n"
        "• Неограниченный трафик и скорость\n"
        "• Поддержка разных устройств\n"
        "• YouTube без рекламы\n"
        "• Сервера NL, USA, FIN\n\n"
        "💡 Выберите период подписки:",
        reply_markup=get_period_keyboard("premium")
    )

# Обработчики выбора периода для базового тарифа
@router.callback_query(lambda c: c.data.startswith("plan_basic_"))
async def plan_basic_period(callback: types.CallbackQuery):
    period = callback.data.replace("plan_basic_", "")
    periods = {"1": (99, 1), "3": (249, 3), "6": (499, 6), "12": (899, 12)}
    
    if period not in periods:
        await callback.answer("Неверный период")
        return
    
    amount, months = periods[period]
    logger.info(f"Пользователь {callback.from_user.id} выбрал базовый тариф на {months} месяц(а/ев), сумма: {amount}₽")
    await callback.answer()
    
    period_text = f"{months} месяц" if months == 1 else f"{months} месяцев"
    await callback.message.edit_text(
        f"💳 <b>Базовый тариф - {period_text}</b>\n\n"
        f"💰 <b>Стоимость:</b> {amount}₽\n\n"
        "📋 <b>Включено:</b>\n"
        "• Неограниченный трафик и скорость\n"
        "• Поддержка разных устройств\n"
        "• YouTube без рекламы\n"
        "• Сервера NL\n\n"
        "💡 Выберите способ оплаты:",
        reply_markup=get_payment_method_keyboard("basic", months, amount)
    )

# Обработчики выбора периода для премиум тарифа
@router.callback_query(lambda c: c.data.startswith("plan_premium_"))
async def plan_premium_period(callback: types.CallbackQuery):
    period = callback.data.replace("plan_premium_", "")
    periods = {"1": (199, 1), "3": (549, 3), "6": (999, 6), "12": (1799, 12)}
    
    if period not in periods:
        await callback.answer("Неверный период")
        return
    
    amount, months = periods[period]
    logger.info(f"Пользователь {callback.from_user.id} выбрал премиум тариф на {months} месяц(а/ев), сумма: {amount}₽")
    await callback.answer()
    
    period_text = f"{months} месяц" if months == 1 else f"{months} месяцев"
    await callback.message.edit_text(
        f"💳 <b>Премиум тариф - {period_text}</b>\n\n"
        f"💰 <b>Стоимость:</b> {amount}₽\n\n"
        "📋 <b>Включено:</b>\n"
        "• Неограниченный трафик и скорость\n"
        "• Поддержка разных устройств\n"
        "• YouTube без рекламы\n"
        "• Сервера NL, USA, FIN\n\n"
        "💡 Выберите способ оплаты:",
        reply_markup=get_payment_method_keyboard("premium", months, amount)
    )


@router.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    logger.info(f"Пользователь {callback.from_user.id} вернулся в главное меню")
    await callback.answer()  # Быстрый ответ сразу
    
    # Удаляем сообщения об оплате USDT, если они есть (не блокируем)
    try:
        from app.routers.crypto_payments import delete_crypto_payment_messages
        asyncio.create_task(delete_crypto_payment_messages(callback.bot, None, callback.from_user.id))
    except:
        pass
    
    # Показываем профиль и подписку как в /start (используем кэш)
    subscription = await get_user_active_subscription(callback.from_user.id, use_cache=True)
    
    # Получаем данные пользователя
    user_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
    if not user_name:
        user_name = callback.from_user.username or "Пользователь"
    
    # Формируем сообщение с профилем
    profile_text = "👤 Профиль:\n"
    profile_text += "• ID: {}\n".format(callback.from_user.id)
    profile_text += "• Имя: {}".format(user_name)
    
    # Формируем сообщение с подпиской (используем единую функцию)
    from app.services.subscription_formatter import format_subscription_info
    subscription_text, has_subscription = format_subscription_info(subscription)
    
    # Объединяем текст
    welcome_text = f"{profile_text}\n\n{subscription_text}"
    
    await callback.message.edit_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=has_subscription)
    )


@router.callback_query(lambda c: c.data == "refresh_info")
async def refresh_info(callback: types.CallbackQuery):
    """Обновление информации о подписке"""
    logger.info(f"Пользователь {callback.from_user.id} обновил информацию")
    await callback.answer("🔄 Информация обновлена")
    
    # Инвалидируем кэш подписки для получения актуальных данных
    from app.services.users import get_user_active_subscription
    subscription = await get_user_active_subscription(callback.from_user.id, use_cache=False)
    
    # Получаем данные пользователя
    user_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
    if not user_name:
        user_name = callback.from_user.username or "Пользователь"
    
    # Формируем сообщение с профилем
    profile_text = "👤 Профиль:\n"
    profile_text += "• ID: {}\n".format(callback.from_user.id)
    profile_text += "• Имя: {}".format(user_name)
    
    # Формируем сообщение с подпиской (используем единую функцию)
    from app.services.subscription_formatter import format_subscription_info
    subscription_text, has_subscription = format_subscription_info(subscription)
    
    # Объединяем текст
    welcome_text = f"{profile_text}\n\n{subscription_text}"
    
    await callback.message.edit_text(
        welcome_text,
        reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=has_subscription)
    )


@router.callback_query(lambda c: c.data == "admin_panel")
async def admin_panel_callback(callback: types.CallbackQuery):
    """Обработчик кнопки админ-панели из главного меню"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    
    logger.info(f"Администратор {callback.from_user.id} открыл панель через кнопку")
    await callback.answer()
    
    from app.services.stats import get_statistics
    from app.keyboards import get_admin_panel_keyboard
    
    stats = await get_statistics()
    
    await callback.message.edit_text(
        f"👑 <b>Панель администратора</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"• Пользователей: {stats['total_users']}\n"
        f"• Активных подписок: {stats['active_subscriptions']}\n"
        f"• Платежей: {stats['total_payments']}\n"
        f"• Доход: {stats['total_revenue']:.2f}₽\n\n"
        f"📈 <b>За сегодня:</b>\n"
        f"• Новых пользователей: {stats['today_users']}\n"
        f"• Платежей: {stats['today_payments']}\n"
        f"• Доход: {stats['today_revenue']:.2f}₽",
        reply_markup=get_admin_panel_keyboard()
    )


@router.message(Command("myid"))
async def cmd_myid(message: types.Message):
    """Команда /myid показывает ваш Telegram ID"""
    user_id = message.from_user.id
    is_admin_user = is_admin(user_id)
    
    text = f"🆔 <b>Ваш Telegram ID:</b> <code>{user_id}</code>\n\n"
    if is_admin_user:
        text += "✅ <b>Статус:</b> Администратор\n"
        text += "👑 У вас есть доступ к админ-панели!"
    else:
        text += "❌ <b>Статус:</b> Обычный пользователь\n"
        text += "💡 Чтобы стать администратором, добавьте ваш ID в .env файл:\n"
        text += f"<code>ADMINS={user_id}</code>"
    
    await message.answer(text, reply_markup=get_main_menu_keyboard(user_id=user_id))


@router.message(Command("profile"))
async def cmd_profile(message: types.Message):
    """Команда /profile показывает профиль пользователя"""
    logger.info(f"Пользователь {message.from_user.id} запросил профиль")
    
    try:
        user = await get_or_create_telegram_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
            create_trial=False  # Убрали пробный период
        )
        
        # Используем кэш для быстрого ответа
        subscription = await get_user_active_subscription(message.from_user.id, use_cache=True)
        
        from app.db.session import SessionLocal
        from app.db.models import Payment
        from sqlalchemy import select, func
        
        total_payments = 0
        total_spent = 0.0
        if SessionLocal:
            async with SessionLocal() as session:
                payments_result = await session.execute(
                    select(func.count(Payment.id), func.coalesce(func.sum(Payment.amount), 0))
                    .where(
                        Payment.telegram_user_id == message.from_user.id,
                        Payment.status == "succeeded"
                    )
                )
                result = payments_result.first()
                if result:
                    total_payments = result[0] or 0
                    total_spent = float(result[1] or 0)
        
        text = f"👤 <b>Ваш профиль</b>\n\n"
        text += f"🆔 <b>ID:</b> {user.telegram_id}\n"
        text += f"👤 <b>Username:</b> @{user.username or 'не указан'}\n"
        text += f"📅 <b>Регистрация:</b> {user.created_at.strftime('%d.%m.%Y') if hasattr(user, 'created_at') and user.created_at else 'неизвестно'}\n\n"
        
        if subscription:
            if subscription.valid_until:
                valid_until_str = subscription.valid_until.strftime("%d.%m.%Y %H:%M")
                days_left = (subscription.valid_until - datetime.utcnow()).days
            else:
                valid_until_str = "Без ограничений"
                days_left = None
            
            plan_name = subscription.plan_name or subscription.plan_code.upper()
            text += f"✅ <b>Подписка:</b> Активна\n"
            text += f"💳 <b>Тариф:</b> {plan_name}\n"
            text += f"📅 <b>Действует до:</b> {valid_until_str}\n"
            if days_left is not None and days_left > 0:
                text += f"⏰ <b>Осталось дней:</b> {days_left}\n"
        else:
            text += f"❌ <b>Подписка:</b> Не активна\n"
        
        text += f"\n💳 <b>Платежи:</b>\n"
        text += f"• Всего успешных: {total_payments}\n"
        text += f"• Потрачено: {total_spent:.2f}₽\n"
        
        await message.answer(text, reply_markup=get_main_menu_keyboard(user_id=message.from_user.id))
        
    except Exception as e:
        logger.error(f"Ошибка при получении профиля: {e}")
        await message.answer(
            "❌ Произошла ошибка при получении информации о профиле. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=message.from_user.id)
        )

