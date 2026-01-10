from aiogram import Router, types, F
from aiogram.filters import Command
from app.config import is_admin
from app.logger import logger
from app.services.stats import get_statistics, get_users_list, get_payments_list
from app.ui.screen_manager import get_screen_manager
from app.ui.screens import ScreenID
from app.navigation.navigator import get_navigator
from app.ui.screens.admin import (
    AdminPanelScreen,
    AdminStatsScreen,
    AdminUsersScreen,
    AdminPaymentsScreen
)

router = Router(name="admin")

@router.message(Command("admin"))
async def admin_panel(message: types.Message):
    """Панель администратора - использует ScreenManager"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора")
        return
    
    logger.info(f"Администратор {message.from_user.id} открыл панель")
    
    stats = await get_statistics()
    
    screen = AdminPanelScreen()
    viewmodel = await screen.create_viewmodel(stats=stats)
    
    screen_manager = get_screen_manager()
    await screen_manager.show_screen(
        screen_id=ScreenID.ADMIN_PANEL,
        message_or_callback=message,
        viewmodel=viewmodel,
        edit=False,
        user_id=message.from_user.id
    )

@router.message(Command("stats"))
async def admin_stats(message: types.Message):
    """Статистика для администратора"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет прав администратора")
        return
    
    stats = await get_statistics()
    
    await message.answer(
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 <b>Пользователи:</b> {stats['total_users']}\n"
        f"💳 <b>Платежи:</b> {stats['total_payments']}\n"
        f"🔄 <b>Активные подписки:</b> {stats['active_subscriptions']}\n"
        f"💰 <b>Доход:</b> {stats['total_revenue']:.2f}₽\n\n"
        f"📈 <b>За сегодня:</b>\n"
        f"• Новых пользователей: {stats['today_users']}\n"
        f"• Платежей: {stats['today_payments']}\n"
        f"• Доход: {stats['today_revenue']:.2f}₽"
    )

@router.callback_query(F.data == "admin_users")
async def admin_users_callback(callback: types.CallbackQuery):
    """Список пользователей - использует ScreenManager через Navigator"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    # Мгновенный фидбек
    await callback.answer()
    
    # Используем ScreenManager для обработки действия через Navigator
    screen_manager = get_screen_manager()
    
    # Определяем текущий экран из Navigator
    navigator = get_navigator()
    current_screen = navigator.get_current_screen(callback.from_user.id) or ScreenID.ADMIN_PANEL
    
    # Обрабатываем действие через Navigator
    await screen_manager.handle_action(
        screen_id=current_screen,
        action="users",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id
    )


@router.callback_query(F.data.startswith("admin_users_page_"))
async def admin_users_page_callback(callback: types.CallbackQuery):
    """Пагинация списка пользователей - использует ScreenManager"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    page = int(callback.data.split("_")[-1])
    await callback.answer()  # Быстрый ответ
    
    users_data = await get_users_list(page=page, page_size=10)
    
    screen = AdminUsersScreen()
    viewmodel = await screen.create_viewmodel(
        users=users_data["users"],
        page=users_data["page"],
        total_pages=users_data["total_pages"],
        total=users_data["total"]
    )
    
    screen_manager = get_screen_manager()
    await screen_manager.show_screen(
        screen_id=ScreenID.ADMIN_USERS,
        message_or_callback=callback,
        viewmodel=viewmodel,
        edit=True,
        user_id=callback.from_user.id
    )


@router.callback_query(F.data == "admin_payments")
async def admin_payments_callback(callback: types.CallbackQuery):
    """Список платежей - использует ScreenManager через Navigator"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    # Мгновенный фидбек
    await callback.answer()
    
    # Используем ScreenManager для обработки действия через Navigator
    screen_manager = get_screen_manager()
    
    # Определяем текущий экран из Navigator
    navigator = get_navigator()
    current_screen = navigator.get_current_screen(callback.from_user.id) or ScreenID.ADMIN_PANEL
    
    # Обрабатываем действие через Navigator
    await screen_manager.handle_action(
        screen_id=current_screen,
        action="payments",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id
    )


@router.callback_query(F.data.startswith("admin_payments_"))
async def admin_payments_filter_callback(callback: types.CallbackQuery):
    """Фильтрация и пагинация платежей"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    data_parts = callback.data.split("_")
    
    if len(data_parts) >= 4 and data_parts[2] == "page":
        page = int(data_parts[3])
        if len(data_parts) > 4:
            status_str = data_parts[4]
            status_map = {
                "all": None,
                "succeeded": "succeeded",
                "pending": "pending",
                "canceled": "canceled",
                "failed": "failed"
            }
            status = status_map.get(status_str, None)
        else:
            status = None
        await callback.answer()  # Быстрый ответ
    else:
        filter_type = data_parts[2]
        status_map = {
            "all": None,
            "succeeded": "succeeded",
            "pending": "pending",
            "canceled": "canceled",
            "failed": "failed"
        }
        status = status_map.get(filter_type)
        page = 1
        await callback.answer()  # Быстрый ответ
    
    payments_data = await get_payments_list(page=page, page_size=10, status=status)
    
    if not payments_data["payments"]:
        filter_text = {
            None: "Все",
            "succeeded": "Успешные",
            "pending": "Ожидающие",
            "canceled": "Отмененные",
            "failed": "Неудачные"
        }.get(status, "Все")
        
        await callback.message.edit_text(
            f"💳 <b>История платежей</b>\n\n"
            f"Фильтр: {filter_text}\n"
            f"Платежей не найдено.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📊 Все", callback_data="admin_payments_all")],
                [types.InlineKeyboardButton(text="✅ Успешные", callback_data="admin_payments_succeeded")],
                [types.InlineKeyboardButton(text="⏳ Ожидают", callback_data="admin_payments_pending")],
                [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
            ])
        )
        return
    
    filter_text = {
        None: "Все",
        "succeeded": "Успешные",
        "pending": "Ожидающие",
        "canceled": "Отмененные",
        "failed": "Неудачные"
    }.get(status, "Все")
    
    text = f"💳 <b>История платежей</b>\n\n"
    text += f"Фильтр: {filter_text}\n"
    text += f"Всего: {payments_data['total']}\n"
    text += f"Страница {payments_data['page']} из {payments_data['total_pages']}\n\n"
    
    for i, payment in enumerate(payments_data["payments"], 1):
        status_emoji = {
            "succeeded": "✅",
            "pending": "⏳",
            "canceled": "❌",
            "failed": "⚠️"
        }.get(payment["status"], "❓")
        
        text += (
            f"{i}. {status_emoji} {payment['amount']:.2f}{payment['currency']} - @{payment['username']}\n"
            f"   Статус: {payment['status']} | {payment['provider']}\n"
        )
    
    keyboard = []
    if payments_data["total_pages"] > 1:
        nav_buttons = []
        if payments_data["page"] > 1:
            nav_buttons.append(types.InlineKeyboardButton(
                text="⬅️ Предыдущая", 
                callback_data=f"admin_payments_page_{payments_data['page'] - 1}_{status or 'all'}"
            ))
        if payments_data["page"] < payments_data["total_pages"]:
            nav_buttons.append(types.InlineKeyboardButton(
                text="Следующая ➡️", 
                callback_data=f"admin_payments_page_{payments_data['page'] + 1}_{status or 'all'}"
            ))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="📊 Все", callback_data="admin_payments_all")],
        [types.InlineKeyboardButton(text="✅ Успешные", callback_data="admin_payments_succeeded")],
        [types.InlineKeyboardButton(text="⏳ Ожидают", callback_data="admin_payments_pending")],
        [types.InlineKeyboardButton(text="⬅️ Назад в админ-панель", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@router.callback_query(F.data == "admin_back")
async def admin_back_callback(callback: types.CallbackQuery):
    """Возврат в админ-панель - использует ScreenManager через Navigator"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора")
        return
    
    # Мгновенный фидбек
    await callback.answer()
    
    # Используем ScreenManager для обработки BACK действия через Navigator
    screen_manager = get_screen_manager()
    
    # Определяем текущий экран из Navigator
    navigator = get_navigator()
    current_screen = navigator.get_current_screen(callback.from_user.id) or ScreenID.ADMIN_PANEL
    
    # Обрабатываем BACK действие через Navigator
    await screen_manager.handle_action(
        screen_id=current_screen,
        action="back",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id
    )


@router.callback_query(F.data.startswith("admin_grant_"))
async def admin_grant_access(callback: types.CallbackQuery):
    """Обработчик выдачи доступа администратором"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    
    try:
        # Парсим callback_data: admin_grant_{request_id}_{tariff}_{months}
        parts = callback.data.split("_")
        if len(parts) != 5:
            await callback.answer("❌ Неверный формат запроса", show_alert=True)
            return
        
        request_id = int(parts[2])
        tariff = parts[3]  # basic или premium
        months = int(parts[4])  # 1 или 3
        
        await callback.answer("⏳ Обрабатываю запрос...")
        
        # Получаем запрос
        from app.services.access_request import get_request_by_id, approve_request
        access_request = await get_request_by_id(request_id)
        
        if not access_request:
            await callback.message.edit_text("❌ Запрос не найден")
            return
        
        if access_request.status != "pending":
            await callback.message.edit_text(f"❌ Запрос уже обработан (статус: {access_request.status})")
            return
        
        telegram_user_id = access_request.telegram_id
        
        # Создаем подписку
        from datetime import datetime, timedelta
        from app.db.session import SessionLocal
        from app.db.models import Subscription, TelegramUser
        from sqlalchemy import select
        from app.repositories.subscription_repo import SubscriptionRepo
        from app.services.payments.yookassa import get_or_create_remna_user_and_get_subscription_url
        from app.services.cache import invalidate_user_cache, invalidate_subscription_cache
        
        async with SessionLocal() as session:
            # Получаем или создаем пользователя
            from app.services.users import get_or_create_telegram_user
            telegram_user = await get_or_create_telegram_user(
                telegram_id=telegram_user_id,
                username=access_request.username,
                first_name=access_request.name,
                create_trial=False
            )
            
            # Определяем plan_name
            plan_name = "Премиум" if tariff == "premium" else "Базовый"
            period_days = months * 30
            valid_until = datetime.utcnow() + timedelta(days=period_days)
            
            # Создаем или обновляем подписку
            sub_repo = SubscriptionRepo(session)
            subscription = await sub_repo.upsert_subscription(
                telegram_user_id=telegram_user_id,
                defaults={
                    'plan_code': tariff,
                    'plan_name': plan_name,
                    'active': True,
                    'valid_until': valid_until,
                    'config_data': {'source': 'admin_grant'}
                }
            )
            
            await session.commit()
            await session.refresh(subscription)
            
            logger.info(f"Подписка создана для пользователя {telegram_user_id}: {tariff} на {months} месяцев (admin grant)")
            
            # Создаем пользователя в Remna API и получаем subscription URL
            try:
                subscription_url = await get_or_create_remna_user_and_get_subscription_url(
                    telegram_user_id=telegram_user_id,
                    subscription_id=subscription.id
                )
                
                if subscription_url:
                    if not subscription.config_data:
                        subscription.config_data = {}
                    subscription.config_data["subscription_url"] = subscription_url
                    await session.commit()
            except Exception as remna_error:
                logger.error(f"Ошибка при создании пользователя в Remna API: {remna_error}")
                # Продолжаем, даже если Remna API недоступна
            
            # Инвалидируем кэш
            await invalidate_user_cache(telegram_user_id)
            await invalidate_subscription_cache(telegram_user_id)
            
            # Помечаем запрос как одобренный
            await approve_request(request_id, callback.from_user.id)
            
            # Отправляем сообщение пользователю
            try:
                user_message = (
                    f"✅ <b>Ваш запрос на доступ одобрен!</b>\n\n"
                    f"💳 <b>Тариф:</b> {plan_name}\n"
                    f"📅 <b>Действует до:</b> {valid_until.strftime('%d.%m.%Y %H:%M')}\n\n"
                    f"🎉 Ваша подписка активирована! Теперь вы можете получить ссылку для настройки VPN."
                )
                
                from app.keyboards import get_subscription_link_keyboard
                await callback.bot.send_message(
                    chat_id=telegram_user_id,
                    text=user_message,
                    reply_markup=get_subscription_link_keyboard(),
                    parse_mode="HTML"
                )
                logger.info(f"Пользователю {telegram_user_id} отправлено сообщение об активации подписки")
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения пользователю {telegram_user_id}: {e}")
            
            # Обновляем сообщение администратора
            await callback.message.edit_text(
                f"✅ <b>Доступ выдан</b>\n\n"
                f"Пользователь: {access_request.name} (@{access_request.username or 'не указан'})\n"
                f"Тариф: {plan_name}\n"
                f"Период: {months} {'месяц' if months == 1 else 'месяца'}\n"
                f"Действует до: {valid_until.strftime('%d.%m.%Y %H:%M')}\n\n"
                f"Пользователю отправлено уведомление."
            )
            
            logger.info(f"Администратор {callback.from_user.id} выдал доступ пользователю {telegram_user_id}: {tariff} на {months} месяцев")
            
    except Exception as e:
        logger.error(f"Ошибка при выдаче доступа администратором {callback.from_user.id}: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        await callback.answer("❌ Произошла ошибка при выдаче доступа", show_alert=True)
        try:
            await callback.message.edit_text("❌ Произошла ошибка при выдаче доступа. Проверьте логи.")
        except:
            pass


@router.callback_query(F.data.startswith("admin_reject_"))
async def admin_reject_access(callback: types.CallbackQuery):
    """Обработчик отклонения запроса администратором"""
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    
    try:
        # Парсим callback_data: admin_reject_{request_id}
        parts = callback.data.split("_")
        if len(parts) != 3:
            await callback.answer("❌ Неверный формат запроса", show_alert=True)
            return
        
        request_id = int(parts[2])
        
        await callback.answer("⏳ Обрабатываю запрос...")
        
        # Получаем запрос
        from app.services.access_request import get_request_by_id, reject_request
        access_request = await get_request_by_id(request_id)
        
        if not access_request:
            await callback.message.edit_text("❌ Запрос не найден")
            return
        
        if access_request.status != "pending":
            await callback.message.edit_text(f"❌ Запрос уже обработан (статус: {access_request.status})")
            return
        
        # Отклоняем запрос
        await reject_request(request_id, callback.from_user.id)
        
        # Отправляем сообщение пользователю
        try:
            await callback.bot.send_message(
                chat_id=access_request.telegram_id,
                text="❌ Ваш запрос на доступ был отклонен администратором.",
                parse_mode="HTML"
            )
            logger.info(f"Пользователю {access_request.telegram_id} отправлено сообщение об отклонении запроса")
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения пользователю {access_request.telegram_id}: {e}")
        
        # Обновляем сообщение администратора
        await callback.message.edit_text(
            f"❌ <b>Запрос отклонен</b>\n\n"
            f"Пользователь: {access_request.name} (@{access_request.username or 'не указан'})\n"
            f"Пользователю отправлено уведомление."
        )
        
        logger.info(f"Администратор {callback.from_user.id} отклонил запрос {request_id}")
        
    except Exception as e:
        logger.error(f"Ошибка при отклонении запроса администратором {callback.from_user.id}: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        await callback.answer("❌ Произошла ошибка при отклонении запроса", show_alert=True)
        try:
            await callback.message.edit_text("❌ Произошла ошибка при отклонении запроса. Проверьте логи.")
        except:
            pass
