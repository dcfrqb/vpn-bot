"""
Роутер для обработки криптовалютных платежей
"""
from aiogram import Router, types, F, Bot
from aiogram.types import BufferedInputFile
from app.logger import logger
from app.services.payments.crypto import (
    create_crypto_payment,
    confirm_crypto_payment,
    get_pending_crypto_payments,
    calculate_usdt_amount,
    get_usdt_amount,
    generate_qr_code
)
from app.keyboards import get_payment_method_keyboard, get_back_to_plans_keyboard, get_main_menu_keyboard
from app.config import settings
from app.db.models import Payment as PaymentModel
from app.db.session import SessionLocal
from sqlalchemy import select


async def delete_crypto_payment_messages(bot: Bot, payment: PaymentModel = None, telegram_user_id: int = None):
    """Удаляет сообщения об оплате USDT (QR-код и кнопки)"""
    try:
        # Если payment не передан, ищем последний pending платеж пользователя
        if not payment and telegram_user_id and SessionLocal:
            async with SessionLocal() as session:
                result = await session.execute(
                    select(PaymentModel)
                    .where(
                        PaymentModel.telegram_user_id == telegram_user_id,
                        PaymentModel.provider == "crypto_usdt_trc20",
                        PaymentModel.status == "pending"
                    )
                    .order_by(PaymentModel.created_at.desc())
                    .limit(1)
                )
                payment = result.scalar_one_or_none()
        
        if payment and payment.payment_metadata:
            metadata = payment.payment_metadata
            chat_id = metadata.get("chat_id")
            qr_message_id = metadata.get("qr_message_id")
            buttons_message_id = metadata.get("buttons_message_id")
            
            if chat_id:
                # Удаляем сообщение с QR-кодом
                if qr_message_id:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=qr_message_id)
                        logger.debug(f"Удалено сообщение с QR-кодом: {qr_message_id}")
                    except Exception as e:
                        logger.debug(f"Не удалось удалить сообщение с QR-кодом: {e}")
                
                # Удаляем сообщение с кнопками
                if buttons_message_id:
                    try:
                        await bot.delete_message(chat_id=chat_id, message_id=buttons_message_id)
                        logger.debug(f"Удалено сообщение с кнопками: {buttons_message_id}")
                    except Exception as e:
                        logger.debug(f"Не удалось удалить сообщение с кнопками: {e}")
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщений об оплате: {e}")

router = Router(name="crypto_payments")


@router.callback_query(F.data.startswith("pay_crypto_"))
async def handle_crypto_payment(callback: types.CallbackQuery):
    """Обработчик выбора оплаты криптовалютой"""
    try:
        # Парсим callback_data: pay_crypto_{plan_code}_{period_months}_{amount}
        parts = callback.data.replace("pay_crypto_", "").split("_")
        
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
                await callback.answer("Неизвестный тариф", show_alert=True)
                return
        else:
            # UI EXCEPTION: быстрый ответ на неверный формат callback_data
            await callback.answer("Неверный формат данных", show_alert=True)
            return
        
        # Определяем название тарифа (только basic/premium поддерживаются для оплаты)
        from app.core.plans import get_plan_name
        if not plan_code or str(plan_code).lower() not in ("basic", "premium"):
            await callback.answer("Неизвестный тариф", show_alert=True)
            return
        plan_name = get_plan_name(plan_code)
        
        period_text = f"{period_months} месяц" if period_months == 1 else f"{period_months} месяцев"
        
        # UI EXCEPTION: быстрый ответ о начале создания платежа
        await callback.answer("Создаю платеж...")
        
        if not settings.CRYPTO_USDT_TRC20_ADDRESS:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text(
                "❌ <b>Ошибка</b>\n\n"
                "Криптовалютные платежи не настроены. Обратитесь к администратору: @dcfrq",
                reply_markup=get_back_to_plans_keyboard()
            )
            return
        
        # Получаем фиксированную сумму в USDT
        usdt_amount = get_usdt_amount(plan_code, period_months)
        
        # Получаем адрес для оплаты (НЕ создаем платеж в БД до подтверждения админом)
        if not settings.CRYPTO_USDT_TRC20_ADDRESS:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text(
                "❌ <b>Ошибка</b>\n\n"
                "Криптовалютные платежи не настроены. Обратитесь к администратору: @dcfrq",
                reply_markup=get_back_to_plans_keyboard()
            )
            return
        
        address = settings.CRYPTO_USDT_TRC20_ADDRESS
        
        # Генерируем QR-код с адресом
        from app.services.payments.crypto import generate_qr_code
        qr_code = generate_qr_code(address)
        
        # Формируем сообщение
        message_text = (
            f"₿ <b>Оплата USDT (TRC20)</b>\n\n"
            f"💳 <b>Тариф:</b> {plan_name} - {period_text}\n"
            f"💰 <b>Сумма:</b> {usdt_amount} USDT\n\n"
            f"📝 <b>Адрес для оплаты:</b>\n"
            f"<code>{address}</code>\n\n"
            f"⚠️ <b>Важно:</b>\n"
            f"• Отправляйте ТОЛЬКО USDT (TRC20)\n"
            f"• Сеть: TRC20 (Tron)\n"
            f"• Проверьте адрес перед отправкой\n"
            f"• После оплаты отправьте хеш транзакции администратору\n\n"
            f"💡 После получения платежа подписка будет активирована автоматически."
        )
        
        # Создаем уникальный ID для платежа (будет использован при подтверждении админом)
        from datetime import datetime
        timestamp = int(datetime.utcnow().timestamp())
        user_id = callback.from_user.id
        payment_id = f"crypto_{user_id}_{timestamp}"
        
        # Формируем callback_data для кнопки "Я оплатил" с полной информацией о платеже
        # Формат: crypto_paid_{plan_code}_{period_months}_{amount_rub}_{user_id}_{timestamp}_{qr_message_id}
        # Это позволит восстановить всю информацию без обращения к БД
        
        # Отправляем QR-код с информацией
        qr_file = BufferedInputFile(
            qr_code.read(),
            filename="payment_qr.png"
        )
        
        qr_message = await callback.message.answer_photo(
            photo=qr_file,
            caption=message_text,
            parse_mode="HTML"
        )
        
        # Сохраняем message_id сообщения с QR-кодом ПЕРЕД отправкой кнопок
        qr_message_id = qr_message.message_id
        
        # Отправляем сообщение с кнопками после QR-кода
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        # Формируем callback_data для возврата к выбору способа оплаты (включая qr_message_id)
        change_payment_callback = f"change_payment_method_{plan_code}_{period_months}_{amount_rub}_{qr_message_id}"
        # Формируем callback_data для кнопки "Я оплатил" с полной информацией о платеже
        # Формат: crypto_paid_{plan_code}_{period_months}_{amount_rub}_{user_id}_{timestamp}_{qr_message_id}
        crypto_paid_callback = f"crypto_paid_{plan_code}_{period_months}_{amount_rub}_{user_id}_{timestamp}_{qr_message_id}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Я оплатил",
                callback_data=crypto_paid_callback
            )],
            [InlineKeyboardButton(
                text="💳 Выбрать другой способ оплаты",
                callback_data=change_payment_callback
            )],
            [InlineKeyboardButton(
                text="ℹ️ Помощь",
                callback_data=f"help_from_crypto_{qr_message_id}"
            )],
            [InlineKeyboardButton(
                text="⬅️ В главное меню",
                callback_data=f"back_to_main_cleanup_{qr_message_id}"
            )]
        ])
        
        # UI EXCEPTION: прямой вызов UI метода
        buttons_message = await callback.message.answer(
            "Используйте кнопки ниже для управления оплатой:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        # Сохраняем message_id сообщений в payment_metadata для последующего удаления
        if payment_id and SessionLocal:
            try:
                async with SessionLocal() as session:
                    result = await session.execute(
                        select(PaymentModel).where(PaymentModel.external_id == payment_id)
                    )
                    payment = result.scalar_one_or_none()
                    if payment:
                        if not payment.payment_metadata:
                            payment.payment_metadata = {}
                        payment.payment_metadata["qr_message_id"] = qr_message.message_id
                        payment.payment_metadata["buttons_message_id"] = buttons_message.message_id
                        payment.payment_metadata["chat_id"] = callback.message.chat.id
                        await session.commit()
            except Exception as e:
                logger.error(f"Ошибка при сохранении message_id: {e}")
        
        # Удаляем исходное сообщение с выбором способа оплаты
        try:
            await callback.message.delete()
        except Exception as e:
            logger.debug(f"Не удалось удалить сообщение: {e}")
        
        logger.info(f"Пользователь {callback.from_user.id} создал крипто-платеж для тарифа {plan_code}")
        
    except Exception as e:
        logger.error(f"Ошибка при создании крипто-платежа: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        # UI EXCEPTION: прямой вызов UI метода
        await callback.message.edit_text(
            "❌ <b>Ошибка</b>\n\n"
            "Произошла ошибка при создании платежа. Попробуйте позже.",
            reply_markup=get_back_to_plans_keyboard()
        )


@router.callback_query(lambda c: c.data.startswith("change_payment_method_"))
async def change_payment_method(callback: types.CallbackQuery):
    """Обработчик возврата к выбору способа оплаты для того же тарифа"""
    # UI EXCEPTION: быстрый ответ на callback перед обработкой
    await callback.answer()
    
    # Парсим параметры из callback_data: change_payment_method_{plan_code}_{period_months}_{amount_rub}_{qr_message_id}
    parts = callback.data.replace("change_payment_method_", "").split("_")
    qr_message_id = None
    
    if len(parts) >= 4:
        plan_code = parts[0]
        period_months = int(parts[1])
        amount_rub = int(parts[2])
        qr_message_id = int(parts[3]) if parts[3].isdigit() else None
    elif len(parts) >= 3:
        plan_code = parts[0]
        period_months = int(parts[1])
        amount_rub = int(parts[2])
    else:
        # Если не удалось распарсить, возвращаем к выбору тарифов
        await buy_subscription_cleanup(callback)
        return
    
    # Удаляем сообщение с QR-кодом если знаем его ID (приоритетный способ)
    if qr_message_id:
        try:
            await callback.bot.delete_message(
                chat_id=callback.from_user.id,
                message_id=qr_message_id
            )
            logger.debug(f"Удалено сообщение с QR-кодом: {qr_message_id}")
        except Exception as e:
            logger.debug(f"Не удалось удалить сообщение с QR-кодом {qr_message_id}: {e}")
    
    # Удаляем сообщения об оплате USDT (включая QR-код) - резервный способ
    await delete_crypto_payment_messages(callback.bot, None, callback.from_user.id)
    
    # Удаляем текущее сообщение с кнопками
    try:
        await callback.message.delete()
    except Exception as e:
        logger.debug(f"Не удалось удалить сообщение: {e}")
    
    # Возвращаемся к выбору способа оплаты для того же тарифа
    from app.keyboards import get_payment_method_keyboard
    from app.core.plans import get_plan_name
    plan_name = get_plan_name(plan_code)
    period_text = f"{period_months} месяц" if period_months == 1 else f"{period_months} месяцев"
    
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=(
            f"💳 <b>{plan_name} - {period_text}</b>\n"
            f"💰 <b>Сумма:</b> {amount_rub}₽\n\n"
            "Выберите способ оплаты:"
        ),
        reply_markup=get_payment_method_keyboard(plan_code, period_months, amount_rub),
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data == "buy_subscription_cleanup")
async def buy_subscription_cleanup(callback: types.CallbackQuery):
    """Обработчик возврата к выбору тарифа с очисткой сообщений об оплате"""
    # UI EXCEPTION: быстрый ответ на callback перед обработкой
    await callback.answer()
    # Удаляем сообщения об оплате USDT
    await delete_crypto_payment_messages(callback.bot, None, callback.from_user.id)
    # Удаляем текущее сообщение с кнопками
    try:
        await callback.message.delete()
    except Exception as e:
        logger.debug(f"Не удалось удалить сообщение: {e}")
    # Показываем меню выбора тарифа
    from app.keyboards import get_plans_keyboard
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=(
            "💳 <b>Покупка подписки</b>\n\n"
            "Выберите тариф:"
        ),
        reply_markup=get_plans_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data.startswith("back_to_main_cleanup_"))
async def back_to_main_cleanup(callback: types.CallbackQuery):
    """Обработчик возврата в главное меню с очисткой сообщений об оплате"""
    # UI EXCEPTION: быстрый ответ на callback перед обработкой
    await callback.answer()
    
    # Парсим message_id из callback_data если есть
    parts = callback.data.replace("back_to_main_cleanup_", "").split("_")
    qr_message_id = int(parts[0]) if parts and parts[0].isdigit() else None
    
    # Удаляем сообщения об оплате USDT
    await delete_crypto_payment_messages(callback.bot, None, callback.from_user.id)
    
    # Удаляем сообщение с QR-кодом если знаем его ID
    if qr_message_id:
        try:
            await callback.bot.delete_message(
                chat_id=callback.from_user.id,
                message_id=qr_message_id
            )
        except Exception as e:
            logger.debug(f"Не удалось удалить сообщение с QR-кодом: {e}")
    # Удаляем текущее сообщение с кнопками
    try:
        await callback.message.delete()
    except Exception as e:
        logger.debug(f"Не удалось удалить сообщение: {e}")
    # Показываем главное меню
    from app.services.users import get_user_active_subscription
    from app.keyboards import get_main_menu_keyboard
    from datetime import datetime
    
    # Используем кэш для быстрого ответа
    subscription = await get_user_active_subscription(callback.from_user.id, use_cache=True)
    
    # Получаем данные пользователя
    user_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
    if not user_name:
        user_name = callback.from_user.username or "Пользователь"
    
    # Формируем сообщение с профилем
    profile_text = "👤 Профиль:\n"
    profile_text += "• ID: {}\n".format(callback.from_user.id)
    profile_text += "• Имя: {}".format(user_name)
    
    # Формируем сообщение с подпиской
    # Формируем сообщение с подпиской (используем единую функцию)
    from app.services.subscription_formatter import format_subscription_info
    subscription_text, has_subscription = format_subscription_info(subscription)
    
    # Объединяем текст
    welcome_text = f"{profile_text}\n\n{subscription_text}"
    
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=welcome_text,
        reply_markup=get_main_menu_keyboard(user_id=callback.from_user.id, has_subscription=has_subscription)
    )

@router.callback_query(F.data.startswith("crypto_paid_"))
async def handle_payment_confirmation(callback: types.CallbackQuery):
    """Обработчик кнопки 'Я оплатил'"""
    try:
        # Парсим информацию из callback_data: crypto_paid_{plan_code}_{period_months}_{amount_rub}_{user_id}_{timestamp}_{qr_message_id}
        parts = callback.data.replace("crypto_paid_", "").split("_")
        
        if len(parts) < 6:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.answer("❌ Ошибка: неверный формат данных", show_alert=True)
            return
        
        plan_code = parts[0]
        period_months = int(parts[1])
        amount_rub = int(parts[2])
        user_id = int(parts[3])
        timestamp = int(parts[4])
        qr_message_id = int(parts[5])
        
        # Проверяем, что это запрос от правильного пользователя
        if callback.from_user.id != user_id:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.answer("❌ Ошибка доступа", show_alert=True)
            return
        
        # Определяем название тарифа через единый справочник
        from app.core.plans import get_plan_name
        plan_name = get_plan_name(plan_code)
        
        period_text = f"{period_months} месяц" if period_months == 1 else f"{period_months} месяцев"
        
        # Создаем payment_id для использования при подтверждении админом
        payment_id = f"crypto_{user_id}_{timestamp}"
        
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("Отправляю уведомление администратору...")
        
        # Удаляем сообщение с QR-кодом если знаем его ID
        if qr_message_id:
            try:
                await callback.bot.delete_message(
                    chat_id=callback.from_user.id,
                    message_id=qr_message_id
                )
                logger.debug(f"Удалено сообщение с QR-кодом: {qr_message_id}")
            except Exception as e:
                logger.debug(f"Не удалось удалить сообщение с QR-кодом {qr_message_id}: {e}")
        
        # Удаляем сообщения об оплате USDT
        await delete_crypto_payment_messages(callback.bot, None, callback.from_user.id)
        
        # Получаем информацию о пользователе
        from app.db.session import SessionLocal
        from app.db.models import TelegramUser
        from sqlalchemy import select
        from datetime import datetime
        
        username = callback.from_user.username or "неизвестен"
        if SessionLocal:
            try:
                async with SessionLocal() as session:
                    user_result = await session.execute(
                        select(TelegramUser).where(TelegramUser.telegram_id == user_id)
                    )
                    user = user_result.scalar_one_or_none()
                    if user and user.username:
                        username = user.username
            except Exception as e:
                logger.debug(f"Ошибка при получении информации о пользователе: {e}")
            
            # Отправляем уведомление админам
            from app.config import settings, is_admin
            # Получаем список админов из настроек
            admin_ids = []
            if settings.ADMINS:
                if isinstance(settings.ADMINS, list):
                    admin_ids = settings.ADMINS
                elif isinstance(settings.ADMINS, str):
                    # Парсим строку с ID через запятую
                    try:
                        admin_ids = [int(admin_id.strip()) for admin_id in settings.ADMINS.split(',') if admin_id.strip()]
                    except:
                        admin_ids = []
            
            # Если админы не найдены, логируем предупреждение
            if not admin_ids:
                logger.warning(f"⚠️ Администраторы не настроены в ADMINS. Платеж {payment_id} не может быть обработан.")
                # UI EXCEPTION: ошибка конфигурации, показываем пользователю
                await callback.message.edit_text(
                    "⚠️ Администраторы не настроены. Обратитесь в поддержку.",
                    reply_markup=get_back_to_plans_keyboard()
                )
                return
            
            # Формируем сообщение для админа
            from app.config import settings
            crypto_address = settings.CRYPTO_USDT_TRC20_ADDRESS or "N/A"
            created_time = datetime.utcnow().strftime('%d.%m.%Y %H:%M')
            
            admin_message = (
                f"💳 <b>Новый крипто-платеж</b>\n\n"
                f"👤 <b>Пользователь:</b> @{username} (ID: {user_id})\n"
                f"💰 <b>Сумма:</b> {amount_rub}₽ ({get_usdt_amount(plan_code, period_months)} USDT)\n"
                f"📝 <b>Тариф:</b> {plan_name} - {period_text}\n"
                f"🆔 <b>ID платежа:</b> <code>{payment_id}</code>\n"
                f"📅 <b>Создан:</b> {created_time}\n"
                f"🔗 <b>Адрес:</b> <code>{crypto_address}</code>\n\n"
                f"Проверьте транзакцию и подтвердите выдачу подписки:"
            )
            
            # Клавиатура для админа
            # Сохраняем информацию о тарифе в callback_data для использования при подтверждении
            # Формат: admin_approve_crypto_{payment_id}_{plan_code}_{period_months}_{amount_rub}
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ Да, выдать подписку",
                    callback_data=f"admin_approve_crypto_{payment_id}_{plan_code}_{period_months}_{amount_rub}"
                )],
                [InlineKeyboardButton(
                    text="❌ Нет, отклонить",
                    callback_data=f"admin_reject_crypto_{payment_id}"
                )]
            ])
            
            # Отправляем всем админам
            sent_count = 0
            for admin_id in admin_ids:
                try:
                    await callback.bot.send_message(
                        chat_id=admin_id,
                        text=admin_message,
                        reply_markup=admin_keyboard,
                        parse_mode="HTML"
                    )
                    sent_count += 1
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления админу {admin_id}: {e}")
            
            if sent_count > 0:
                # Удаляем текущее сообщение с кнопками
                try:
                    await callback.message.delete()
                except Exception as e:
                    logger.debug(f"Не удалось удалить сообщение: {e}")
                
                # Отправляем новое сообщение
                await callback.bot.send_message(
                    chat_id=callback.from_user.id,
                    text=(
                        "✅ <b>Уведомление отправлено администратору</b>\n\n"
                        "Администратор проверит платеж и активирует вашу подписку.\n"
                        "Вы получите уведомление после подтверждения.\n\n"
                        "💡 <b>Пока ожидаете:</b>\n"
                        "• Изучите инструкции по настройке VPN\n"
                        "• Подготовьте устройство для подключения"
                    ),
                    reply_markup=get_main_menu_keyboard(user_id=user_id),
                    parse_mode="HTML"
                )
                return
            else:
                # UI EXCEPTION: ошибка отправки уведомления администратору
                await callback.message.edit_text(
                    "⚠️ Не удалось отправить уведомление администратору.\n"
                    "Пожалуйста, свяжитесь с поддержкой вручную.",
                    reply_markup=get_back_to_plans_keyboard()
                )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке подтверждения оплаты: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        # UI EXCEPTION: прямой вызов UI метода
        await callback.message.edit_text(
            "❌ Произошла ошибка. Попробуйте позже или обратитесь в поддержку.",
            reply_markup=get_back_to_plans_keyboard()
        )


@router.callback_query(F.data.startswith("admin_approve_crypto_"))
async def admin_approve_crypto_payment(callback: types.CallbackQuery):
    """Обработчик подтверждения крипто-платежа админом"""
    from app.config import is_admin
    
    if not is_admin(callback.from_user.id):
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    
    try:
        # Парсим callback_data: admin_approve_crypto_{payment_id}_{plan_code}_{period_months}_{amount_rub}
        # payment_id имеет формат: crypto_{user_id}_{timestamp} (3 части через _)
        # После payment_id идут: plan_code, period_months, amount_rub
        data_str = callback.data.replace("admin_approve_crypto_", "")
        
        if not data_str.startswith("crypto_"):
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text("❌ Неверный формат данных: payment_id должен начинаться с 'crypto_'")
            return
        
        # Разделяем все части
        all_parts = data_str.split("_")
        
        # payment_id состоит из 3 частей: crypto, user_id, timestamp
        # Затем идут: plan_code, period_months, amount_rub
        if len(all_parts) < 6:  # минимум 6 частей
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text("❌ Неверный формат данных: недостаточно параметров")
            logger.error(f"Недостаточно частей в callback_data: {all_parts}, длина: {len(all_parts)}")
            return
        
        # Собираем payment_id из первых 3 частей
        payment_id = "_".join(all_parts[0:3])  # crypto_{user_id}_{timestamp}
        plan_code = all_parts[3]  # plan_code (basic или premium)
        
        try:
            period_months = int(all_parts[4])
            amount_rub = int(all_parts[5])
        except (ValueError, IndexError) as e:
            logger.error(f"Ошибка парсинга параметров: {e}, all_parts: {all_parts}")
            # UI EXCEPTION: ошибка парсинга данных платежа
            await callback.message.edit_text(f"❌ Ошибка парсинга данных: {e}")
            return
        
        # Парсим payment_id: crypto_{user_id}_{timestamp}
        payment_parts = payment_id.replace("crypto_", "").split("_")
        if len(payment_parts) < 2:
            logger.error(f"Неверный формат payment_id: {payment_id}, parts: {payment_parts}")
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text("❌ Неверный формат ID платежа")
            return
        
        try:
            user_id = int(payment_parts[0])
            timestamp = int(payment_parts[1])
        except ValueError as e:
            logger.error(f"Ошибка парсинга payment_id: {payment_id}, parts: {payment_parts}, error: {e}")
            # UI EXCEPTION: ошибка парсинга данных платежа
            await callback.message.edit_text(f"❌ Ошибка парсинга ID платежа: {e}")
            return
        
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("Подтверждаю платеж...")
        
        # Создаем платеж в БД при подтверждении админом
        from app.db.session import SessionLocal
        from app.db.models import Payment as PaymentModel, TelegramUser
        from sqlalchemy import select, text
        from datetime import datetime
        from app.config import settings
        import json
        
        if not SessionLocal:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text("❌ База данных не настроена")
            return
        
        async with SessionLocal() as session:
            # Проверяем, существует ли уже такой платеж
            result = await session.execute(
                select(PaymentModel).where(PaymentModel.external_id == payment_id)
            )
            payment = result.scalar_one_or_none()
            
            # Если платеж не существует, создаем его
            if not payment:
                # Получаем информацию о пользователе
                user_result = await session.execute(
                    select(TelegramUser).where(TelegramUser.telegram_id == user_id)
                )
                user = user_result.scalar_one_or_none()
                
                if not user:
                    # UI EXCEPTION: прямой вызов UI метода
                    await callback.message.edit_text(f"❌ Пользователь {user_id} не найден")
                    return
                
                # Используем информацию о тарифе из callback_data
                from app.core.plans import get_plan_name
                plan_name = get_plan_name(plan_code)
                
                period_text = f"{period_months} месяц" if period_months == 1 else f"{period_months} месяцев"
                description = f"CRS VPN - {plan_name} ({period_text})"
                
                # Создаем платеж в БД используя прямой SQL для обхода кэша asyncpg
                from sqlalchemy import text
                import json
                
                # Используем прямой SQL INSERT для обхода кэша схемы asyncpg
                # НЕ указываем id явно - используем DEFAULT для автоматической генерации через sequence
                insert_sql = text("""
                    INSERT INTO payments (
                        telegram_user_id, provider, external_id, amount, currency, 
                        status, description, payment_metadata, paid_at, created_at, updated_at
                    ) VALUES (
                        :telegram_user_id, :provider, :external_id, :amount, :currency,
                        :status, :description, :payment_metadata, :paid_at, :created_at, :updated_at
                    ) RETURNING id
                """)
                
                now = datetime.utcnow()
                result = await session.execute(
                    insert_sql,
                    {
                        "telegram_user_id": user_id,
                        "provider": "crypto_usdt_trc20",
                        "external_id": payment_id,
                        "amount": amount_rub,
                        "currency": "RUB",
                        "status": "succeeded",
                        "description": description,
                        "payment_metadata": json.dumps({
                            "crypto_address": settings.CRYPTO_USDT_TRC20_ADDRESS or "N/A",
                            "network": settings.CRYPTO_NETWORK or "TRC20",
                            "created_at": now.isoformat(),
                            "approved_by_admin": True,
                            "admin_id": callback.from_user.id,
                            "plan_code": plan_code,
                            "period_months": period_months
                        }),
                        "paid_at": now,
                        "created_at": now,
                        "updated_at": now
                    }
                )
                payment_db_id = result.scalar()
                await session.commit()
                
                # Получаем созданный платеж
                payment_result = await session.execute(
                    select(PaymentModel).where(PaymentModel.id == payment_db_id)
                )
                payment = payment_result.scalar_one()
                
                logger.info(f"Создан крипто-платеж {payment_id} (ID: {payment_db_id}) после подтверждения админом")
            
            # Обрабатываем успешный платеж
            from app.services.payments.yookassa import handle_successful_payment
            
            try:
                await handle_successful_payment(
                    session=session,
                    payment_id=payment.id,
                    telegram_user_id=payment.telegram_user_id,
                    amount=float(payment.amount),
                    description=payment.description,
                    bot=callback.bot
                )
                logger.info(f"Платеж {payment_id} успешно обработан, подписка активирована для пользователя {payment.telegram_user_id}")
                
                # UI EXCEPTION: подтверждение платежа администратором
                await callback.message.edit_text(
                    f"✅ Платеж подтвержден\n\n"
                    f"Пользователю {payment.telegram_user_id} отправлено уведомление об активации подписки."
                )
            except Exception as payment_error:
                logger.error(f"Ошибка при обработке успешного платежа: {payment_error}")
                import traceback
                logger.debug(traceback.format_exc())
                # Отправляем уведомление пользователю вручную, если handle_successful_payment не сработал
                try:
                    await callback.bot.send_message(
                        chat_id=payment.telegram_user_id,
                        text=(
                            "✅ Платеж подтвержден\n\n"
                            "Ваш платеж был подтвержден администратором.\n"
                            "Подписка активируется, пожалуйста, подождите несколько минут.\n\n"
                            "Если подписка не активировалась, напишите в поддержку: @dcfrq"
                        )
                    )
                    logger.info(f"Резервное уведомление отправлено пользователю {payment.telegram_user_id}")
                except Exception as notify_error:
                    logger.error(f"Ошибка при отправке резервного уведомления: {notify_error}")
                
                # UI EXCEPTION: прямой вызов UI метода
                await callback.message.edit_text(
                    f"⚠️ Платеж подтвержден, но возникла ошибка при активации подписки.\n\n"
                    f"Пользователю {payment.telegram_user_id} отправлено уведомление.\n"
                    f"Ошибка: {str(payment_error)[:100]}"
                )
            
    except Exception as e:
        logger.error(f"Ошибка при подтверждении платежа админом: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        error_msg = str(e)
        # Убираем HTML теги и специальные символы из сообщения об ошибке
        error_msg_clean = error_msg.replace("<", "").replace(">", "").replace("&", "и")
        try:
            # UI EXCEPTION: прямой вызов UI метода
            await callback.message.edit_text(f"❌ Ошибка: {error_msg_clean[:200]}")
        except Exception as edit_error:
            # Если не удалось отредактировать, отправляем новое сообщение
            logger.error(f"Ошибка при редактировании сообщения: {edit_error}")
            try:
                # UI EXCEPTION: прямой вызов UI метода
                await callback.message.answer(f"❌ Ошибка: {error_msg_clean[:200]}")
            except:
                pass


@router.callback_query(F.data.startswith("admin_reject_crypto_"))
async def admin_reject_crypto_payment(callback: types.CallbackQuery):
    """Обработчик отклонения крипто-платежа админом"""
    from app.config import is_admin
    
    if not is_admin(callback.from_user.id):
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    
    try:
        payment_id = callback.data.replace("admin_reject_crypto_", "")
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("Отклоняю платеж...")
        
        # Извлекаем user_id из payment_id (формат: crypto_{user_id}_{timestamp})
        user_id = None
        if payment_id.startswith("crypto_"):
            payment_parts = payment_id.replace("crypto_", "").split("_")
            if len(payment_parts) >= 1:
                try:
                    user_id = int(payment_parts[0])
                except ValueError:
                    logger.error(f"Не удалось извлечь user_id из payment_id: {payment_id}")
        
        # Получаем информацию о платеже
        from app.db.session import SessionLocal
        from app.db.models import Payment as PaymentModel
        from sqlalchemy import select
        
        async with SessionLocal() as session:
            result = await session.execute(
                select(PaymentModel).where(PaymentModel.external_id == payment_id)
            )
            payment = result.scalar_one_or_none()
            
            # Если платеж найден, обновляем статус
            if payment:
                payment.status = "canceled"
                await session.commit()
                user_id = payment.telegram_user_id
                logger.info(f"Платеж {payment_id} отклонен, статус обновлен в БД")
            
            # Отправляем уведомление пользователю (даже если платеж не найден в БД)
            if user_id:
                try:
                    await callback.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "❌ Платеж отклонен\n\n"
                            "Ваш платеж был отклонен администратором.\n\n"
                            "Если вы считаете это ошибкой или хотите предоставить документы об оплате, "
                            "напишите в поддержку: @dcfrq"
                        )
                    )
                    logger.info(f"Уведомление об отклонении отправлено пользователю {user_id}")
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")
                
                # UI EXCEPTION: прямой вызов UI метода
                await callback.message.edit_text(
                    f"❌ Платеж отклонен\n\n"
                    f"Пользователю {user_id} отправлено уведомление."
                )
            else:
                # UI EXCEPTION: прямой вызов UI метода
                await callback.message.edit_text(
                    "⚠️ Платеж не найден и не удалось определить пользователя"
                )
                
    except Exception as e:
        logger.error(f"Ошибка при отклонении платежа: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        error_msg = str(e)
        # UI EXCEPTION: ошибка при отклонении платежа
        await callback.message.edit_text(f"❌ Ошибка: {error_msg}")


@router.callback_query(F.data.startswith("help_from_crypto_"))
async def help_from_crypto(callback: types.CallbackQuery):
    """Обработчик кнопки 'Помощь' из криптоплатежа"""
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Парсим message_id из callback_data
    parts = callback.data.replace("help_from_crypto_", "").split("_")
    qr_message_id = int(parts[0]) if parts and parts[0].isdigit() else None
    
    # Удаляем сообщения об оплате USDT (включая QR-код)
    await delete_crypto_payment_messages(callback.bot, None, callback.from_user.id)
    
    # Удаляем сообщение с QR-кодом если знаем его ID
    if qr_message_id:
        try:
            await callback.bot.delete_message(
                chat_id=callback.from_user.id,
                message_id=qr_message_id
            )
        except Exception as e:
            logger.debug(f"Не удалось удалить сообщение с QR-кодом: {e}")
    
    # Удаляем текущее сообщение с кнопками
    try:
        await callback.message.delete()
    except Exception as e:
        logger.debug(f"Не удалось удалить сообщение: {e}")
    
    # Показываем помощь через ScreenManager (новый экран)
    from app.ui.screen_manager import get_screen_manager
    # UI EXCEPTION: импорт ScreenID для передачи в ScreenManager
    from app.ui.screens import ScreenID
    
    screen_manager = get_screen_manager()
    await screen_manager.handle_action(
        screen_id=ScreenID.HELP,
        action="open",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id
    )

@router.callback_query(F.data == "vpn_instructions")
async def show_vpn_instructions(callback: types.CallbackQuery):
    """Показывает инструкции по настройке VPN"""
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    instructions_text = (
        "📖 <b>Инструкции по настройке VPN</b>\n\n"
        "🔗 <b>Полезные ссылки:</b>\n\n"
        "📱 <b>Для мобильных устройств:</b>\n"
        "• <a href='https://clash.lbyczf.com/'>Clash for Android</a>\n"
        "• <a href='https://apps.apple.com/app/shadowrocket/id932747118'>Shadowrocket (iOS)</a>\n\n"
        "💻 <b>Для компьютеров:</b>\n"
        "• <a href='https://github.com/Fndroid/clash_for_windows_pkg/releases'>Clash for Windows</a>\n"
        "• <a href='https://github.com/yichengchen/clashX'>ClashX (macOS)</a>\n\n"
        "📚 <b>Документация:</b>\n"
        "• <a href='https://github.com/Dreamacro/clash/wiki'>Clash Wiki</a>\n"
        "• <a href='https://clash.lbyczf.com/'>Официальный сайт Clash</a>\n\n"
        "💡 <b>Как использовать:</b>\n"
        "1. После активации подписки получите ссылку конфигурации\n"
        "2. Установите одно из приложений выше\n"
        "3. Импортируйте конфигурацию по ссылке\n"
        "4. Включите VPN и наслаждайтесь безопасным интернетом!\n\n"
        "🆘 <b>Нужна помощь?</b>\n"
        "Обратитесь к администратору: @dcfrq"
    )
    
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="back_to_main"
        )]
    ])
    
    # UI EXCEPTION: показ инструкций по VPN (команда)
    await callback.message.edit_text(
        instructions_text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=False
    )


# Админские команды для подтверждения платежей
@router.message(F.text.startswith("/confirm_crypto"))
async def confirm_payment_command(message: types.Message):
    """Команда для подтверждения крипто-платежа (только для админов)"""
    from app.config import is_admin
    
    if not is_admin(message.from_user.id):
        # UI EXCEPTION: прямой вызов UI метода
        await message.answer("❌ У вас нет прав администратора")
        return
    
    try:
        # Формат: /confirm_crypto <payment_id> [transaction_hash]
        parts = message.text.split()
        if len(parts) < 2:
            # UI EXCEPTION: показ справки по использованию команды (команда)
            await message.answer(
                "Использование: /confirm_crypto <payment_id> [transaction_hash]\n\n"
                "Пример: /confirm_crypto crypto_123456789_1234567890"
            )
            return
        
        payment_id = parts[1]
        transaction_hash = parts[2] if len(parts) > 2 else None
        
        success = await confirm_crypto_payment(payment_id, transaction_hash)
        
        if success:
            # Обрабатываем успешный платеж
            from app.services.payments.yookassa import handle_successful_payment
            from app.db.session import SessionLocal
            from app.db.models import Payment as PaymentModel
            from sqlalchemy import select
            
            try:
                async with SessionLocal() as session:
                    result = await session.execute(
                        select(PaymentModel).where(PaymentModel.external_id == payment_id)
                    )
                    payment = result.scalar_one_or_none()
                    
                    if payment and payment.status == "succeeded":
                        await handle_successful_payment(
                            session=session,
                            payment_id=payment.id,
                            telegram_user_id=payment.telegram_user_id,
                            amount=float(payment.amount),
                            description=payment.description or "CRS VPN 30 дней",
                            bot=message.bot
                        )
                        # UI EXCEPTION: ответ администратору о подтверждении платежа
                        await message.answer(f"✅ Платеж {payment_id} подтвержден и обработан. Пользователю отправлено уведомление.")
                    else:
                        # UI EXCEPTION: ответ администратору о статусе платежа
                        await message.answer(f"⚠️ Платеж {payment_id} не найден или не в статусе succeeded")
            except Exception as e:
                logger.error(f"Ошибка при обработке успешного крипто-платежа: {e}")
                # UI EXCEPTION: ошибка при обработке платежа
                await message.answer(f"✅ Платеж подтвержден, но ошибка при обработке: {e}")
        else:
            # UI EXCEPTION: платеж не подтвержден (подтверждение)
            await message.answer(f"❌ Не удалось подтвердить платеж {payment_id}")
            
    except Exception as e:
        logger.error(f"Ошибка при подтверждении платежа: {e}")
        # UI EXCEPTION: общая ошибка при подтверждении платежа
        await message.answer(f"❌ Ошибка: {e}")


@router.message(F.text == "/pending_crypto")
async def list_pending_payments(message: types.Message):
    """Команда для просмотра ожидающих крипто-платежей (только для админов)"""
    from app.config import is_admin
    
    if not is_admin(message.from_user.id):
        # UI EXCEPTION: проверка прав администратора для команды
        await message.answer("❌ У вас нет прав администратора")
        return
    
    try:
        payments = await get_pending_crypto_payments()
        
        if not payments:
            # UI EXCEPTION: ответ о пустом списке платежей (список)
            await message.answer("📭 Ожидающих крипто-платежей нет")
            return
        
        text = f"⏳ <b>Ожидающие крипто-платежи ({len(payments)}):</b>\n\n"
        
        for payment in payments[:10]:  # Показываем первые 10
            text += (
                f"💳 <b>Платеж:</b> {payment['external_id']}\n"
                f"👤 <b>Пользователь:</b> {payment['user_id']}\n"
                f"💰 <b>Сумма:</b> {payment['amount']} {payment['currency']}\n"
                f"📅 <b>Создан:</b> {payment['created_at'].strftime('%d.%m.%Y %H:%M')}\n"
                f"🔗 <b>Адрес:</b> <code>{payment['metadata'].get('crypto_address', 'N/A')}</code>\n\n"
            )
        
        if len(payments) > 10:
            text += f"... и еще {len(payments) - 10} платежей"
        
        # UI EXCEPTION: показ списка ожидающих платежей администратору
        await message.answer(text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка при получении списка платежей: {e}")
        # UI EXCEPTION: ошибка при получении списка платежей
        await message.answer(f"❌ Ошибка: {e}")

