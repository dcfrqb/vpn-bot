from aiogram import Router, types
from aiogram.filters import CommandStart, Command
from aiogram.exceptions import TelegramBadRequest
import asyncio
import time
import uuid
import traceback
from app.logger import logger
from app.services.users import get_user_active_subscription, get_or_create_telegram_user
from app.services.sync_service import SyncService, RemnaUnavailableError, SyncResult
from app.services.connection import can_user_connect
from app.services.cache import get_cached_sync_result, invalidate_sync_cache
from app.routers.menu_builder import build_main_menu_text, MenuData
from app.routers.subscription_view import SubscriptionViewModel, create_subscription_view_model
from app.keyboards import (
    get_main_menu_keyboard,
    get_plans_keyboard,
    get_payment_keyboard,
    get_back_to_plans_keyboard,
    get_help_keyboard,
    get_subscription_info_keyboard,
    get_payment_method_keyboard,
    get_period_keyboard,
    get_inactive_subscription_keyboard
)
# UI EXCEPTION: импорт ScreenID для передачи в ScreenManager
from app.ui.screens import ScreenID
from app.ui.helpers import get_main_menu_viewmodel
from app.config import is_admin
from app.navigation.callback_schema import CallbackAction
from datetime import datetime

router = Router(name="start")

@router.message(CommandStart())
async def cmd_start(m: types.Message):
    """
    Обработчик команды /start - HARD RESET UI
    
    /start ВСЕГДА:
    - Очищает backstack
    - Очищает flow anchor
    - Сбрасывает current_screen на MAIN_MENU
    - Показывает MAIN_MENU через Navigator с RenderMode.OPEN
    """
    start_time = time.time()
    request_id = str(uuid.uuid4())[:8].upper()
    telegram_id = m.from_user.id
    
    # Получаем Navigator и ScreenManager
    from app.navigation.navigator import get_navigator
    from app.ui.screen_manager import get_screen_manager
    navigator = get_navigator()
    screen_manager = get_screen_manager()
    
    # ЛОГИРУЕМ состояние ДО /start
    screen_before = navigator.get_current_screen(telegram_id)
    backstack_before = navigator.get_backstack(telegram_id)
    
    logger.info(
        f"[{request_id}] /start: user_id={telegram_id}, "
        f"screen_before={screen_before.value if screen_before else 'None'}, "
        f"backstack_size={len(backstack_before)}"
    )
    
    # HARD RESET: ВСЕГДА очищаем состояние
    navigator.clear_backstack(telegram_id)
    navigator.clear_flow_anchor(telegram_id)
    navigator._set_current_screen(telegram_id, ScreenID.MAIN_MENU)
    
    # Получаем параметры из команды /start (для обработки deep links)
    command_args = m.text.split()[1:] if len(m.text.split()) > 1 else []
    start_param = command_args[0] if command_args else None
    
    tg_name = f"{m.from_user.first_name or ''} {m.from_user.last_name or ''}".strip()
    if not tg_name:
        tg_name = m.from_user.username or f"User_{telegram_id}"
    
    # При /start используем кэш для быстрого ответа, но проверяем актуальность
    # Если кэш свежий (< 60 сек) - используем его, иначе синхронизируемся с Remna
    sync_result = None
    
    try:
        sync_service = SyncService()
        try:
            # ОПТИМИЗАЦИЯ: Сначала проверяем кэш для быстрого ответа
            # Если кэш свежий - используем его, иначе делаем синхронизацию
            from app.services.cache import get_cached_sync_result
            cached = await get_cached_sync_result(m.from_user.id)
            
            if cached:
                # Проверяем, насколько свежий кэш
                cached_time = cached.get('updated_at')
                if cached_time:
                    try:
                        if isinstance(cached_time, str):
                            cached_dt = datetime.fromisoformat(cached_time.replace('Z', '+00:00'))
                            if cached_dt.tzinfo:
                                cached_dt = cached_dt.replace(tzinfo=None)
                        else:
                            cached_dt = cached_time
                            if hasattr(cached_dt, 'tzinfo') and cached_dt.tzinfo:
                                cached_dt = cached_dt.replace(tzinfo=None)
                        
                        cache_age_seconds = (datetime.utcnow() - cached_dt).total_seconds()
                        
                        # Если кэш свежий (< 60 сек) - используем его для быстрого ответа
                        if cache_age_seconds < 60:
                            logger.debug(f"Используем свежий кэш для {m.from_user.id} (возраст: {cache_age_seconds:.1f}с)")
                            from app.services.sync_service import SyncResult
                            expires_at = None
                            if cached.get('expires_at'):
                                try:
                                    if isinstance(cached['expires_at'], str):
                                        expires_at = datetime.fromisoformat(cached['expires_at'])
                                    else:
                                        expires_at = cached['expires_at']
                                except Exception:
                                    pass
                            
                            sync_result = SyncResult(
                                is_new_user_created=False,
                                user_remna_uuid=cached.get('remna_uuid'),
                                subscription_status=cached.get('status', 'none'),
                                expires_at=expires_at,
                                source='cache'
                            )
                            
                            # Запускаем синхронизацию в фоне для обновления кэша
                            async def background_sync():
                                try:
                                    await sync_service.sync_user_and_subscription(
                                        telegram_id=m.from_user.id,
                                        tg_name=tg_name,
                                        use_fallback=True,
                                        use_cache=False,
                                        force_sync=True
                                    )
                                    logger.debug(f"Фоновая синхронизация завершена для {m.from_user.id}")
                                except Exception as bg_e:
                                    logger.debug(f"Ошибка фоновой синхронизации: {bg_e}")
                            
                            asyncio.create_task(background_sync())
                        else:
                            # Кэш устарел - делаем синхронизацию
                            logger.debug(f"Кэш устарел для {m.from_user.id} (возраст: {cache_age_seconds:.1f}с), синхронизируемся")
                            sync_result = await sync_service.sync_user_and_subscription(
                                telegram_id=m.from_user.id,
                                tg_name=tg_name,
                                use_fallback=True,
                                use_cache=False,
                                force_sync=True
                            )
                    except Exception as cache_check_e:
                        logger.debug(f"Ошибка проверки кэша: {cache_check_e}, делаем синхронизацию")
                        sync_result = await sync_service.sync_user_and_subscription(
                            telegram_id=m.from_user.id,
                            tg_name=tg_name,
                            use_fallback=True,
                            use_cache=False,
                            force_sync=True
                        )
                else:
                    # Нет времени в кэше - делаем синхронизацию
                    sync_result = await sync_service.sync_user_and_subscription(
                        telegram_id=m.from_user.id,
                        tg_name=tg_name,
                        use_fallback=True,
                        use_cache=False,
                        force_sync=True
                    )
            else:
                # Кэша нет - делаем синхронизацию
                sync_result = await sync_service.sync_user_and_subscription(
                    telegram_id=m.from_user.id,
                    tg_name=tg_name,
                    use_fallback=True,
                    use_cache=False,
                    force_sync=True
                )
        except RemnaUnavailableError:
            # Remna недоступна, но у нас есть fallback
            logger.warning(f"Remna недоступна для {m.from_user.id}, используем fallback")
            # Fallback уже должен был вернуть результат
            if not sync_result:
                # Если fallback не сработал, используем данные из БД напрямую
                subscription = await get_user_active_subscription(m.from_user.id, use_cache=True)
                subscription_status = "none"
                expires_at = None
                if subscription:
                    if subscription.active and subscription.valid_until:
                        if subscription.valid_until > datetime.utcnow():
                            subscription_status = "active"
                            expires_at = subscription.valid_until
                        else:
                            subscription_status = "expired"
                            expires_at = subscription.valid_until
                
                sync_result = SyncResult(
                    is_new_user_created=False,
                    user_remna_uuid=None,
                    subscription_status=subscription_status,
                    expires_at=expires_at,
                    source="db_fallback"
                )
        except Exception as e:
            logger.error(
                f"Ошибка синхронизации для пользователя {m.from_user.id}: {e}\n"
                f"Traceback: {traceback.format_exc()}"
            )
            # Используем данные из БД как последний fallback
            subscription = await get_user_active_subscription(m.from_user.id, use_cache=True)
            subscription_status = "none"
            expires_at = None
            if subscription:
                if subscription.active and subscription.valid_until:
                    if subscription.valid_until > datetime.utcnow():
                        subscription_status = "active"
                        expires_at = subscription.valid_until
                    else:
                        subscription_status = "expired"
                        expires_at = subscription.valid_until
            
            sync_result = SyncResult(
                is_new_user_created=False,
                user_remna_uuid=None,
                subscription_status=subscription_status,
                expires_at=expires_at,
                source="db_fallback"
            )
        
        # Проверяем, что sync_result не None
        if not sync_result:
            logger.error(f"sync_result is None для пользователя {m.from_user.id} - это не должно происходить!")
            # UI EXCEPTION: прямой вызов UI метода
            await m.answer(
                "❌ <b>Произошла ошибка</b>\n\n"
                "Пожалуйста, попробуйте позже или обратитесь в поддержку: @dcfrq",
                parse_mode="HTML"
            )
            return
        
        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Синхронизация для {m.from_user.id} завершена: "
            f"status={sync_result.subscription_status}, "
            f"is_new={sync_result.is_new_user_created}, "
            f"remna_uuid={sync_result.user_remna_uuid}, "
            f"время: {elapsed_ms:.2f}мс, "
            f"source={sync_result.source}"
        )
    except TelegramBadRequest as e:
        # Ошибка форматирования HTML/разметки
        logger.exception(
            f"[{request_id}] TelegramBadRequest в /start (user={telegram_id}): {e}",
            extra={
                "request_id": request_id,
                "telegram_id": telegram_id,
                "handler": "cmd_start",
                "error_type": "TelegramBadRequest",
                "error_message": str(e)
            }
        )
        # UI EXCEPTION: прямой вызов UI метода
        await m.answer(
            f"❌ <b>Произошла ошибка форматирования сообщения</b>\n\n"
            f"Код ошибки: <code>{request_id}</code>\n\n"
            "Пожалуйста, попробуйте позже или обратитесь в поддержку: @dcfrq",
            parse_mode="HTML"
        )
        return
    except RemnaUnavailableError as e:
        # Remna недоступна - это не критично, используем fallback
        logger.warning(
            f"[{request_id}] Remna недоступна для {telegram_id}: {e}",
            extra={
                "request_id": request_id,
                "telegram_id": telegram_id,
                "handler": "cmd_start",
                "error_type": "RemnaUnavailableError"
            }
        )
        # Продолжаем с fallback данными
    except Exception as e:
        # Общая ошибка - логируем с полным traceback
        # traceback уже импортирован в начале файла
        logger.exception(
            f"[{request_id}] Критическая ошибка в /start (user={telegram_id}): {e}",
            extra={
                "request_id": request_id,
                "telegram_id": telegram_id,
                "handler": "cmd_start",
                "error_type": type(e).__name__,
                "error_message": str(e),
                "traceback": traceback.format_exc()
            }
        )
        # UI EXCEPTION: прямой вызов UI метода
        await m.answer(
            f"❌ <b>Произошла ошибка</b>\n\n"
            f"Код ошибки: <code>{request_id}</code>\n\n"
            "Пожалуйста, попробуйте позже или обратитесь в поддержку: @dcfrq",
            parse_mode="HTML"
        )
        return
    
    # Обработка deep links (если есть) - запускается в фоне, не блокирует MAIN_MENU
    if start_param == "payment_success":
        # Пользователь вернулся после оплаты через ЮКассу
        logger.info(f"[{request_id}] Deep link: payment_success для user_id={telegram_id}")
        
        # Обрабатываем в фоне, не блокируем показ MAIN_MENU
        async def handle_payment_success_deeplink():
            try:
                from app.db.session import SessionLocal
                from app.db.models import Payment as PaymentModel
                from sqlalchemy import select
                from app.services.payments.yookassa import check_payment_status, handle_successful_payment
                
                subscription = await get_user_active_subscription(telegram_id, use_cache=False)
                
                if subscription:
                    # Подписка уже активирована - отправляем уведомление отдельным сообщением
                    await m.bot.send_message(
                        chat_id=telegram_id,
                        text=(
                            "✅ <b>Спасибо за оплату!</b>\n\n"
                            "Ваш платеж успешно обработан. Подписка активирована!\n\n"
                            "Теперь вы можете получить ссылку для настройки VPN."
                        ),
                        reply_markup=get_subscription_info_keyboard(has_subscription=True),
                        parse_mode="HTML"
                    )
                    return
                
                # Проверяем последний платеж (быстрая проверка)
                if SessionLocal:
                    try:
                        async with SessionLocal() as session:
                            result = await session.execute(
                                select(PaymentModel)
                                .where(
                                    PaymentModel.telegram_user_id == telegram_id,
                                    PaymentModel.provider == "yookassa"
                                )
                                .order_by(PaymentModel.created_at.desc())
                                .limit(1)
                            )
                            payment = result.scalar_one_or_none()
                            
                            if payment and payment.status == "pending":
                                payment_status = await check_payment_status(payment.external_id)
                                if payment_status and payment_status.get("status") == "succeeded":
                                    await handle_successful_payment(
                                        session=session,
                                        payment_id=payment.id,
                                        telegram_user_id=telegram_id,
                                        amount=payment_status.get("amount", payment.amount),
                                        description=payment.description or "CRS VPN 30 дней",
                                        bot=m.bot
                                    )
                                    await m.bot.send_message(
                                        chat_id=telegram_id,
                                        text=(
                                            "✅ <b>Платеж успешно обработан!</b>\n\n"
                                            "Ваша подписка активирована. Теперь вы можете получить ссылку для настройки VPN."
                                        ),
                                        reply_markup=get_subscription_info_keyboard(has_subscription=True),
                                        parse_mode="HTML"
                                    )
                                    logger.info(f"Платеж {payment.external_id} обработан после возврата пользователя")
                    except Exception as e:
                        logger.error(f"Ошибка при обработке возврата после оплаты: {e}")
            except Exception as e:
                logger.error(f"Ошибка в фоновой задаче обработки deep link: {e}")
        
        # Запускаем обработку deep link в фоне
        asyncio.create_task(handle_payment_success_deeplink())
        
    # ВСЕГДА показываем MAIN_MENU через Navigator с RenderMode.OPEN
    # (независимо от start_param - deep links обрабатываются отдельно в фоне)
    
    # Получаем ViewModel для главного меню
    # Используем уже синхронизированные данные (sync_result уже получен выше)
    # Но передаем force_sync=True, чтобы get_main_menu_viewmodel использовал актуальные данные
    viewmodel = await get_main_menu_viewmodel(
        telegram_id=telegram_id,
        first_name=m.from_user.first_name,
        last_name=m.from_user.last_name,
        username=m.from_user.username,
        use_cache=False,  # При /start не используем кэш для актуальности
        force_sync=True   # Принудительная синхронизация для актуальности данных
    )
    
    # Используем Navigator для навигации к MAIN_MENU
    nav_result = navigator.handle(
        action=CallbackAction.OPEN,
        current_screen=ScreenID.MAIN_MENU,  # Уже установлен выше
        payload={"target_screen": ScreenID.MAIN_MENU.value},
        user_id=telegram_id,
        user_role="admin" if is_admin(telegram_id) else "user"
    )
    
    # Показываем экран через ScreenManager
    await screen_manager.show_screen(
        screen_id=nav_result.target_screen,
        message_or_callback=m,
        viewmodel=viewmodel,
        edit=False  # Всегда новое сообщение при /start
    )
    
    # ЛОГИРУЕМ состояние ПОСЛЕ /start
    screen_after = navigator.get_current_screen(telegram_id)
    backstack_after = navigator.get_backstack(telegram_id)
    
    logger.info(
        f"[{request_id}] /start завершен: "
        f"screen_before={screen_before.value if screen_before else 'None'} -> "
        f"screen_after={screen_after.value if screen_after else 'None'}, "
        f"backstack_before={len(backstack_before)} -> backstack_after={len(backstack_after)}, "
        f"duration={(time.time() - start_time) * 1000:.2f}ms"
    )

@router.callback_query(lambda c: c.data == "buy_subscription")
async def buy_subscription(callback: types.CallbackQuery):
    """Обработчик кнопки 'Купить подписку' - использует ScreenManager"""
    logger.info(f"Пользователь {callback.from_user.id} нажал 'Купить подписку'")
    # Мгновенный фидбек
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Удаляем сообщения об оплате USDT в фоне (не блокируем)
    try:
        from app.routers.crypto_payments import delete_crypto_payment_messages
        asyncio.create_task(delete_crypto_payment_messages(callback.bot, None, callback.from_user.id))
    except:
        pass
    
    # Показываем экран выбора тарифов
    # UI EXCEPTION: импорт для передачи в ScreenManager
    from app.ui.screens.subscription import SubscriptionPlansScreen
    screen = SubscriptionPlansScreen()
    viewmodel = await screen.create_viewmodel()
    
    from app.ui.screen_manager import get_screen_manager
    screen_manager = get_screen_manager()
    await screen_manager.navigate(
        from_screen_id=ScreenID.MAIN_MENU,
        to_screen_id=ScreenID.SUBSCRIPTION_PLANS,
        message_or_callback=callback,
        viewmodel=viewmodel,
        edit=True
    )

@router.callback_query(lambda c: c.data == "my_plan")
async def my_plan(callback: types.CallbackQuery):
    """Обработчик кнопки 'Мой тариф'"""
    logger.info(f"Пользователь {callback.from_user.id} нажал 'Мой тариф'")
    # Мгновенный фидбек
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
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
        
        # UI EXCEPTION: legacy handler для отображения информации о тарифе (будет переведен на ScreenManager)
        await callback.message.edit_text(
            text,
            reply_markup=get_subscription_info_keyboard(has_subscription=True)
        )
    else:
        # Используем кэш для быстрого ответа
        subscription = await get_user_active_subscription(callback.from_user.id, use_cache=True)
        has_sub = subscription is not None
        
        # UI EXCEPTION: legacy handler для отображения информации о тарифе (будет переведен на ScreenManager)
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
    """Обработчик кнопки 'Подключиться' - использует ScreenManager"""
    logger.info(f"Пользователь {callback.from_user.id} нажал 'Подключиться'")
    # Даем мгновенный фидбек через callback.answer()
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer("⏳ Получаем ссылку подписки...")
    
    # Используем ScreenManager для обработки CONNECT flow
    # Проверка подписки выполняется внутри _handle_connect_flow с принудительной синхронизацией с Remna
    from app.ui.screen_manager import get_screen_manager
    screen_manager = get_screen_manager()
    await screen_manager.handle_action(
        screen_id=ScreenID.CONNECT,
        action="open",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id
    )

@router.callback_query(lambda c: c.data == "help")
async def help_info(callback: types.CallbackQuery):
    """Обработчик кнопки 'Помощь' - использует ScreenManager"""
    logger.info(f"Пользователь {callback.from_user.id} нажал 'Помощь'")
    # Мгновенный фидбек
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Показываем экран помощи через handle_action (FLOW action)
    from app.ui.screen_manager import get_screen_manager
    screen_manager = get_screen_manager()
    await screen_manager.handle_action(
        screen_id=ScreenID.HELP,
        action="open",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id
    )

@router.callback_query(lambda c: c.data == "plan_basic")
async def plan_basic(callback: types.CallbackQuery):
    """Обработчик выбора базового тарифа - использует ScreenManager"""
    logger.info(f"Пользователь {callback.from_user.id} выбрал тариф 'Базовый'")
    # Мгновенный фидбек
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Используем ScreenManager для показа экрана деталей тарифа
    # UI EXCEPTION: импорт для передачи в ScreenManager
    from app.ui.screens.subscription import SubscriptionPlanDetailScreen
    screen = SubscriptionPlanDetailScreen()
    viewmodel = await screen.create_viewmodel(
        plan_code="basic",
        plan_name="Базовый тариф",
        period_months=0,  # Период еще не выбран
        amount=0,
        features=[
            "Неограниченный трафик и скорость",
            "Поддержка разных устройств",
            "YouTube без рекламы",
            "Сервера NL"
        ]
    )
    
    from app.ui.screen_manager import get_screen_manager
    screen_manager = get_screen_manager()
    await screen_manager.navigate(
        from_screen_id=ScreenID.SUBSCRIPTION_PLANS,
        to_screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
        message_or_callback=callback,
        viewmodel=viewmodel,
        edit=True
    )

@router.callback_query(lambda c: c.data == "plan_premium")
async def plan_premium(callback: types.CallbackQuery):
    """Обработчик выбора премиум тарифа - использует ScreenManager"""
    logger.info(f"Пользователь {callback.from_user.id} выбрал тариф 'Премиум'")
    # Мгновенный фидбек
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Используем ScreenManager для показа экрана деталей тарифа
    # UI EXCEPTION: импорт для передачи в ScreenManager
    from app.ui.screens.subscription import SubscriptionPlanDetailScreen
    screen = SubscriptionPlanDetailScreen()
    viewmodel = await screen.create_viewmodel(
        plan_code="premium",
        plan_name="Премиум тариф",
        period_months=0,  # Период еще не выбран
        amount=0,
        features=[
            "Неограниченный трафик и скорость",
            "Поддержка разных устройств",
            "YouTube без рекламы",
            "Сервера NL, USA, FIN"
        ]
    )
    
    from app.ui.screen_manager import get_screen_manager
    screen_manager = get_screen_manager()
    await screen_manager.navigate(
        from_screen_id=ScreenID.SUBSCRIPTION_PLANS,
        to_screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
        message_or_callback=callback,
        viewmodel=viewmodel,
        edit=True
    )

# Обработчики выбора периода для базового тарифа
@router.callback_query(lambda c: c.data.startswith("plan_basic_"))
async def plan_basic_period(callback: types.CallbackQuery):
    """Обработчик выбора периода для базового тарифа - использует ScreenManager"""
    period = callback.data.replace("plan_basic_", "")
    periods = {"1": (99, 1), "3": (249, 3), "6": (499, 6), "12": (899, 12)}
    
    if period not in periods:
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("Неверный период")
        return
    
    amount, months = periods[period]
    logger.info(f"Пользователь {callback.from_user.id} выбрал базовый тариф на {months} месяц(а/ев), сумма: {amount}₽")
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Создаем ViewModel для детального экрана тарифа
    # UI EXCEPTION: импорт для передачи в ScreenManager
    from app.ui.screens.subscription import SubscriptionPlanDetailScreen
    screen = SubscriptionPlanDetailScreen()
    viewmodel = await screen.create_viewmodel(
        plan_code="basic",
        plan_name="Базовый тариф",
        period_months=months,
        amount=amount,
        features=[
            "Неограниченный трафик и скорость",
            "Поддержка разных устройств",
            "YouTube без рекламы",
            "Сервера NL"
        ]
    )
    
    # Показываем экран через ScreenManager
    from app.ui.screen_manager import get_screen_manager
    screen_manager = get_screen_manager()
    await screen_manager.navigate(
        from_screen_id=ScreenID.SUBSCRIPTION_PLANS,
        to_screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
        message_or_callback=callback,
        viewmodel=viewmodel,
        edit=True
    )

# Обработчики выбора периода для премиум тарифа
@router.callback_query(lambda c: c.data.startswith("plan_premium_"))
async def plan_premium_period(callback: types.CallbackQuery):
    """Обработчик выбора периода для премиум тарифа - использует ScreenManager"""
    period = callback.data.replace("plan_premium_", "")
    periods = {"1": (199, 1), "3": (549, 3), "6": (999, 6), "12": (1799, 12)}
    
    if period not in periods:
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("Неверный период")
        return
    
    amount, months = periods[period]
    logger.info(f"Пользователь {callback.from_user.id} выбрал премиум тариф на {months} месяц(а/ев), сумма: {amount}₽")
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Создаем ViewModel для детального экрана тарифа
    # UI EXCEPTION: импорт для передачи в ScreenManager
    from app.ui.screens.subscription import SubscriptionPlanDetailScreen
    screen = SubscriptionPlanDetailScreen()
    viewmodel = await screen.create_viewmodel(
        plan_code="premium",
        plan_name="Премиум тариф",
        period_months=months,
        amount=amount,
        features=[
            "Неограниченный трафик и скорость",
            "Поддержка разных устройств",
            "YouTube без рекламы",
            "Сервера NL, USA, FIN"
        ]
    )
    
    # Показываем экран через ScreenManager
    from app.ui.screen_manager import get_screen_manager
    screen_manager = get_screen_manager()
    await screen_manager.navigate(
        from_screen_id=ScreenID.SUBSCRIPTION_PLANS,
        to_screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
        message_or_callback=callback,
        viewmodel=viewmodel,
        edit=True
    )


@router.callback_query(lambda c: c.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    """Обработчик возврата в главное меню - использует ScreenManager через Navigator"""
    logger.info(f"Пользователь {callback.from_user.id} вернулся в главное меню")
    # Мгновенный фидбек
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    # Удаляем сообщения об оплате USDT, если они есть (не блокируем)
    try:
        from app.routers.crypto_payments import delete_crypto_payment_messages
        asyncio.create_task(delete_crypto_payment_messages(callback.bot, None, callback.from_user.id))
    except:
        pass
    
    # Используем ScreenManager для обработки BACK действия через Navigator
    from app.ui.screen_manager import get_screen_manager
    screen_manager = get_screen_manager()
    navigator = get_navigator()
    
    # Определяем текущий экран из Navigator
    current_screen = navigator.get_current_screen(callback.from_user.id) or ScreenID.MAIN_MENU
    
    # Обрабатываем BACK действие через Navigator
    await screen_manager.handle_action(
        screen_id=current_screen,
        action="back",
        payload="-",
        message_or_callback=callback,
        user_id=callback.from_user.id
    )


@router.callback_query(lambda c: c.data == "refresh_info")
async def refresh_info(callback: types.CallbackQuery):
    """
    Обновление информации о подписке - ПРИНУДИТЕЛЬНАЯ синхронизация с Remna API.
    Использует ScreenManager для отображения главного меню.
    """
    start_time = time.time()
    telegram_id = callback.from_user.id
    logger.info(f"Пользователь {telegram_id} нажал кнопку 'Обновить' - принудительная синхронизация с Remna")
    
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer("🔄 Обновление данных из Remna...")
    
    # Инвалидируем кэш перед принудительной синхронизацией
    await invalidate_sync_cache(telegram_id)
    
    # Синхронизируем пользователя с Remna (ПРИНУДИТЕЛЬНО, только Remna API)
    sync_service = SyncService()
    tg_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
    if not tg_name:
        tg_name = callback.from_user.username or f"User_{telegram_id}"
    
    sync_result = None
    remna_success = False
    remna_start_time = time.time()
    
    try:
        sync_result = await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_name=tg_name,
            use_fallback=False,      # НЕ используем fallback из БД
            use_cache=False,          # НЕ используем кэш
            force_sync=True,          # Принудительная синхронизация
            force_remna=True          # ПРИНУДИТЕЛЬНО только Remna API
        )
        remna_elapsed_ms = (time.time() - remna_start_time) * 1000
        remna_success = True
        logger.info(
            f"Принудительная синхронизация с Remna успешна для {telegram_id}: "
            f"status={sync_result.subscription_status}, "
            f"время Remna API: {remna_elapsed_ms:.2f}мс"
        )
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("✅ Данные обновлены из Remna")
        
    except RemnaUnavailableError as e:
        remna_elapsed_ms = (time.time() - remna_start_time) * 1000
        logger.error(
            f"Принудительная синхронизация с Remna не удалась для {telegram_id}: "
            f"Remna API недоступна (время попытки: {remna_elapsed_ms:.2f}мс) - {e}"
        )
        # UI EXCEPTION: обработка ошибки Remna недоступна (legacy handler, будет переведен на ScreenManager)
        # Показываем ошибку пользователю, НЕ используем старые данные
        await callback.message.edit_text(
            "❌ <b>Не удалось обновить данные</b>\n\n"
            "Сервис Remna временно недоступен. Пожалуйста, попробуйте позже.\n\n"
            "Если проблема сохраняется, обратитесь в поддержку: @dcfrq",
            reply_markup=get_main_menu_keyboard(user_id=telegram_id, has_subscription=None),
            parse_mode="HTML"
        )
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ Remna недоступна")
        return  # Выходим, не показываем старые данные
        
    except Exception as e:
        import traceback
        remna_elapsed_ms = (time.time() - remna_start_time) * 1000
        logger.error(
            f"Ошибка принудительной синхронизации с Remna для {telegram_id} "
            f"(время попытки: {remna_elapsed_ms:.2f}мс): {e}\n"
            f"Traceback: {traceback.format_exc()}"
        )
        # UI EXCEPTION: обработка ошибки синхронизации (legacy handler, будет переведен на ScreenManager)
        # Показываем ошибку пользователю, НЕ используем старые данные
        await callback.message.edit_text(
            "❌ <b>Ошибка при обновлении данных</b>\n\n"
            "Произошла ошибка при обращении к Remna API. Пожалуйста, попробуйте позже.\n\n"
            "Если проблема сохраняется, обратитесь в поддержку: @dcfrq",
            reply_markup=get_main_menu_keyboard(user_id=telegram_id, has_subscription=None),
            parse_mode="HTML"
        )
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ Ошибка обновления")
        return  # Выходим, не показываем старые данные
    
    # Проверяем, что sync_result получен (должен быть, если не было исключения)
    if not sync_result:
        logger.error(f"sync_result is None после принудительной синхронизации для {telegram_id}")
        # UI EXCEPTION: обработка ошибки отсутствия sync_result (legacy handler, будет переведен на ScreenManager)
        await callback.message.edit_text(
            "❌ <b>Ошибка при обновлении данных</b>\n\n"
            "Не удалось получить данные из Remna. Пожалуйста, попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=telegram_id, has_subscription=None),
            parse_mode="HTML"
        )
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ Ошибка обновления")
        return
    
    elapsed_ms = (time.time() - start_time) * 1000
    logger.info(
        f"Кнопка 'Обновить' обработана для {telegram_id}: "
        f"status={sync_result.subscription_status}, "
        f"общее время: {elapsed_ms:.2f}мс, "
        f"remna_success={remna_success}"
    )
    
    # Получаем ViewModel для главного меню с обновленными данными
    viewmodel = await get_main_menu_viewmodel(
        telegram_id=telegram_id,
        first_name=callback.from_user.first_name,
        last_name=callback.from_user.last_name,
        username=callback.from_user.username,
        use_cache=False,
        force_sync=False  # Уже синхронизировали выше
    )
    
    # Показываем экран через ScreenManager
    from app.ui.screen_manager import get_screen_manager
    screen_manager = get_screen_manager()
    await screen_manager.show_screen(
        screen_id=ScreenID.MAIN_MENU,
        message_or_callback=callback,
        viewmodel=viewmodel,
        edit=True
    )


@router.callback_query(lambda c: c.data == "admin_panel")
async def admin_panel_callback(callback: types.CallbackQuery):
    """Обработчик кнопки админ-панели из главного меню - использует ScreenManager"""
    if not is_admin(callback.from_user.id):
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ У вас нет прав администратора", show_alert=True)
        return
    
    logger.info(f"Администратор {callback.from_user.id} открыл панель через кнопку")
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    
    from app.services.stats import get_statistics
    # UI EXCEPTION: импорт для передачи в ScreenManager
    from app.ui.screens.admin import AdminPanelScreen
    
    stats = await get_statistics()
    
    screen = AdminPanelScreen()
    viewmodel = await screen.create_viewmodel(stats=stats)
    
    from app.ui.screen_manager import get_screen_manager
    screen_manager = get_screen_manager()
    await screen_manager.navigate(
        from_screen_id=ScreenID.MAIN_MENU,
        to_screen_id=ScreenID.ADMIN_PANEL,
        message_or_callback=callback,
        viewmodel=viewmodel,
        edit=True
    )


@router.message(Command("solokhin"))
async def cmd_solokhin(message: types.Message):
    """Промокод /solokhin — 1 месяц basic для новых пользователей (без подписок)"""
    user_id = message.from_user.id
    logger.info(f"Пользователь {user_id} вызвал промокод /solokhin")

    from sqlalchemy import select, func
    from app.db.session import SessionLocal
    from app.db.models import Subscription
    from app.repositories.subscription_repo import SubscriptionRepo
    from app.services.users import get_or_create_telegram_user
    from app.services.cache import invalidate_user_cache, invalidate_subscription_cache

    if not SessionLocal:
        await message.answer("❌ Сервис временно недоступен. Попробуйте позже.")
        return

    async with SessionLocal() as session:
        result = await session.execute(
            select(func.count(Subscription.id)).where(
                Subscription.telegram_user_id == user_id
            )
        )
        count = result.scalar() or 0

    if count > 0:
        await message.answer(
            "Промокод доступен только новым пользователям.",
            reply_markup=get_main_menu_keyboard(user_id=user_id)
        )
        return

    async with SessionLocal() as session:
        await get_or_create_telegram_user(
            telegram_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            create_trial=False
        )

        from datetime import datetime, timedelta
        now = datetime.utcnow()
        expires_at = now + timedelta(days=30)

        sub_repo = SubscriptionRepo(session)
        subscription = await sub_repo.upsert_subscription(
            telegram_user_id=user_id,
            defaults={
                "plan_code": "basic",
                "plan_name": "Базовый",
                "active": True,
                "valid_until": expires_at,
                "config_data": {"source": "promo_solokhin"},
            },
        )
        await session.commit()
        await session.refresh(subscription)

        try:
            from app.services.payments.yookassa import get_or_create_remna_user_and_get_subscription_url
            subscription_url = await get_or_create_remna_user_and_get_subscription_url(
                telegram_user_id=user_id,
                subscription_id=subscription.id
            )
            if subscription_url:
                if not subscription.config_data:
                    subscription.config_data = {}
                subscription.config_data["subscription_url"] = subscription_url
                await session.commit()
        except Exception as remna_err:
            logger.warning(f"Remna API недоступна для промо solokhin: {remna_err}")

        await invalidate_user_cache(user_id)
        await invalidate_subscription_cache(user_id)

    await message.answer(
        "Вам выдан 1 месяц бесплатного доступа 🎉",
        reply_markup=get_main_menu_keyboard(user_id=user_id)
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
    
    # UI EXCEPTION: прямой вызов UI метода
    await message.answer(text, reply_markup=get_main_menu_keyboard(user_id=user_id))


@router.message(Command("profile"))
async def cmd_profile(message: types.Message):
    """Команда /profile показывает профиль пользователя - использует ScreenManager"""
    logger.info(f"Пользователь {message.from_user.id} запросил профиль")
    
    try:
        from app.ui.helpers import get_profile_viewmodel
        viewmodel = await get_profile_viewmodel(message.from_user.id)
        
        from app.ui.screen_manager import get_screen_manager
        screen_manager = get_screen_manager()
        await screen_manager.show_screen(
            screen_id=ScreenID.PROFILE,
            message_or_callback=message,
            viewmodel=viewmodel,
            edit=False
        )
    except Exception as e:
        logger.error(f"Ошибка при получении профиля: {e}")
        # Показываем ошибку через ScreenManager (будет реализовано в ШАГ 4)
        # UI EXCEPTION: прямой вызов UI метода
        await message.answer(
            "❌ Произошла ошибка при получении информации о профиле. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=message.from_user.id)
        )


@router.message(Command("friend"))
async def cmd_friend(message: types.Message):
    """Команда /friend - запрос на выдачу VPN-доступа с подтверждением администратором"""
    user_id = message.from_user.id
    logger.info(f"Пользователь {user_id} вызвал команду /friend")
    
    try:
        # ПРИНУДИТЕЛЬНАЯ проверка подписки через Remna API (без кэша и fallback)
        logger.info(f"Принудительная проверка подписки через Remna API для команды /friend (user_id={user_id})")
        
        tg_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip()
        if not tg_name:
            tg_name = message.from_user.username or f"User_{user_id}"
        
        sync_service = SyncService()
        try:
            sync_result = await sync_service.sync_user_and_subscription(
                telegram_id=user_id,
                tg_name=tg_name,
                use_fallback=False,      # НЕ используем fallback из БД
                use_cache=False,          # НЕ используем кэш
                force_sync=True,          # Принудительная синхронизация
                force_remna=True         # ПРИНУДИТЕЛЬНО только Remna API
            )
            
            logger.info(
                f"Результат проверки подписки для /friend (user_id={user_id}): "
                f"status={sync_result.subscription_status}, "
                f"expires_at={sync_result.expires_at}"
            )
            
            # Проверяем статус подписки из Remna
            if sync_result.subscription_status == "active":
                # Есть активная подписка в Remna - запрещаем создание запроса
                logger.info(f"Пользователь {user_id} имеет активную подписку в Remna, /friend недоступен")
                # UI EXCEPTION: прямой вызов UI метода
                await message.answer(
                    "❌ У вас уже есть активная подписка. Команда /friend недоступна при активной подписке.",
                    reply_markup=get_main_menu_keyboard(user_id=user_id)
                )
                return
            
            # Подписка отсутствует или истекла - можно создать запрос
            logger.info(
                f"Пользователь {user_id} не имеет активной подписки в Remna "
                f"(status={sync_result.subscription_status}), можно создать запрос"
            )
            
        except RemnaUnavailableError as e:
            # Remna недоступна - не используем старые данные
            logger.error(f"Remna API недоступна для проверки подписки в /friend (user_id={user_id}): {e}")
            # UI EXCEPTION: прямой вызов UI метода
            await message.answer(
                "❌ Не удалось проверить статус подписки. Попробуйте позже.",
                reply_markup=get_main_menu_keyboard(user_id=user_id)
            )
            return
        except Exception as e:
            logger.error(f"Ошибка при проверке подписки через Remna для /friend (user_id={user_id}): {e}")
            # UI EXCEPTION: прямой вызов UI метода
            await message.answer(
                "❌ Не удалось проверить статус подписки. Попробуйте позже.",
                reply_markup=get_main_menu_keyboard(user_id=user_id)
            )
            return
        
        # Проверяем возможность создания запроса (rate-limit и pending)
        from app.services.access_request import can_create_request
        can_create, error_message = await can_create_request(user_id)
        
        if not can_create:
            # UI EXCEPTION: прямой вызов UI метода
            await message.answer(
                f"❌ {error_message}",
                reply_markup=get_main_menu_keyboard(user_id=user_id)
            )
            return
        
        # Показываем подтверждение
        from app.keyboards import get_friend_request_keyboard
        # UI EXCEPTION: прямой вызов UI метода
        await message.answer(
            "❓ Вы хотите попросить доступ к VPN у администратора?",
            reply_markup=get_friend_request_keyboard()
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке команды /friend для пользователя {user_id}: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        # UI EXCEPTION: прямой вызов UI метода
        await message.answer(
            "❌ Произошла ошибка. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=user_id)
        )


@router.callback_query(lambda c: c.data == "friend_request_yes")
async def friend_request_yes(callback: types.CallbackQuery):
    """Обработчик кнопки 'Да' для запроса на доступ"""
    user_id = callback.from_user.id
    logger.info(f"Пользователь {user_id} подтвердил запрос на доступ")
    
    try:
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer()
        
        # ПРИНУДИТЕЛЬНАЯ проверка подписки через Remna API (без кэша и fallback)
        logger.info(f"Принудительная проверка подписки через Remna API для friend_request_yes (user_id={user_id})")
        
        tg_name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
        if not tg_name:
            tg_name = callback.from_user.username or f"User_{user_id}"
        
        sync_service = SyncService()
        try:
            sync_result = await sync_service.sync_user_and_subscription(
                telegram_id=user_id,
                tg_name=tg_name,
                use_fallback=False,      # НЕ используем fallback из БД
                use_cache=False,          # НЕ используем кэш
                force_sync=True,          # Принудительная синхронизация
                force_remna=True         # ПРИНУДИТЕЛЬНО только Remna API
            )
            
            logger.info(
                f"Результат проверки подписки для friend_request_yes (user_id={user_id}): "
                f"status={sync_result.subscription_status}"
            )
            
            # Проверяем статус подписки из Remna
            if sync_result.subscription_status == "active":
                # Есть активная подписка в Remna - запрещаем создание запроса
                logger.info(f"Пользователь {user_id} имеет активную подписку в Remna, запрос не может быть создан")
                # UI EXCEPTION: обработка ошибки активной подписки (legacy handler, будет переведен на ScreenManager)
                await callback.message.edit_text(
                    "❌ У вас уже есть активная подписка. Запрос не может быть создан.",
                    reply_markup=None
                )
                return
            
            # Подписка отсутствует или истекла - можно создать запрос
            logger.info(
                f"Пользователь {user_id} не имеет активной подписки в Remna "
                f"(status={sync_result.subscription_status}), можно создать запрос"
            )
            
        except RemnaUnavailableError as e:
            # Remna недоступна - не используем старые данные
            logger.error(f"Remna API недоступна для проверки подписки в friend_request_yes (user_id={user_id}): {e}")
            # UI EXCEPTION: обработка ошибки Remna недоступна (legacy handler, будет переведен на ScreenManager)
            await callback.message.edit_text(
                "❌ Не удалось проверить статус подписки. Попробуйте позже.",
                reply_markup=None
            )
            return
        except Exception as e:
            logger.error(f"Ошибка при проверке подписки через Remna для friend_request_yes (user_id={user_id}): {e}")
            # UI EXCEPTION: обработка ошибки проверки подписки (legacy handler, будет переведен на ScreenManager)
            await callback.message.edit_text(
                "❌ Не удалось проверить статус подписки. Попробуйте позже.",
                reply_markup=None
            )
            return
        
        # Проверяем возможность создания запроса
        from app.services.access_request import can_create_request, has_pending_request
        can_create, error_message = await can_create_request(user_id)
        
        if not can_create:
            # UI EXCEPTION: обработка ошибки создания запроса (legacy handler, будет переведен на ScreenManager)
            await callback.message.edit_text(
                f"❌ {error_message}",
                reply_markup=None
            )
            return
        
        # Создаем запрос
        from app.services.access_request import create_access_request
        name = f"{callback.from_user.first_name or ''} {callback.from_user.last_name or ''}".strip()
        if not name:
            name = callback.from_user.username or f"User_{user_id}"
        
        access_request = await create_access_request(
            telegram_id=user_id,
            name=name,
            username=callback.from_user.username
        )
        
        if not access_request:
            # UI EXCEPTION: обработка ошибки создания запроса (legacy handler, будет переведен на ScreenManager)
            await callback.message.edit_text(
                "❌ Ошибка при создании запроса. Попробуйте позже.",
                reply_markup=None
            )
            return
        
        # UI EXCEPTION: уведомление об отправке запроса (legacy handler, будет переведен на ScreenManager)
        # Отправляем сообщение пользователю
        await callback.message.edit_text(
            "⏳ Запрос отправлен администратору. Ожидайте подтверждения.",
            reply_markup=None
        )
        
        # Отправляем сообщение администраторам
        from app.config import settings
        from app.keyboards import get_admin_access_request_keyboard
        
        admin_message = (
            f"👤 <b>Запрос на доступ</b>\n\n"
            f"Имя: {name}\n"
            f"Username: @{callback.from_user.username if callback.from_user.username else 'не указан'}\n"
            f"Telegram ID: <code>{user_id}</code>\n\n"
            f"Выберите вариант доступа:"
        )
        
        for admin_id in settings.ADMINS:
            try:
                await callback.bot.send_message(
                    chat_id=admin_id,
                    text=admin_message,
                    reply_markup=get_admin_access_request_keyboard(access_request.id),
                    parse_mode="HTML"
                )
                logger.info(f"Запрос на доступ {access_request.id} отправлен администратору {admin_id}")
            except Exception as e:
                logger.error(f"Ошибка при отправке запроса администратору {admin_id}: {e}")
        
    except Exception as e:
        logger.error(f"Ошибка при обработке подтверждения запроса для пользователя {user_id}: {e}")
        # UI EXCEPTION: прямой вызов UI метода
        await callback.answer("❌ Произошла ошибка. Попробуйте позже.", show_alert=True)


@router.callback_query(lambda c: c.data == "friend_request_no")
async def friend_request_no(callback: types.CallbackQuery):
    """Обработчик кнопки 'Нет' для запроса на доступ"""
    # UI EXCEPTION: прямой вызов UI метода
    await callback.answer()
    # UI EXCEPTION: уведомление об отмене запроса (legacy handler, будет переведен на ScreenManager)
    await callback.message.edit_text("Запрос отменён", reply_markup=None)

