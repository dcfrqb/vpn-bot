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
from app.config import is_admin, settings
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
    
    _uname = f"@{m.from_user.username}" if m.from_user.username else "no_username"
    logger.info(
        f"[{request_id}] /start: user_id={telegram_id}, username={_uname}, "
        f"first_name={m.from_user.first_name!r}, last_name={m.from_user.last_name!r}, "
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
                                        tg_username=m.from_user.username,
                                        tg_first_name=m.from_user.first_name,
                                        tg_last_name=m.from_user.last_name,
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
                                tg_username=m.from_user.username,
                                tg_first_name=m.from_user.first_name,
                                tg_last_name=m.from_user.last_name,
                                use_fallback=True,
                                use_cache=False,
                                force_sync=True
                            )
                    except Exception as cache_check_e:
                        logger.debug(f"Ошибка проверки кэша: {cache_check_e}, делаем синхронизацию")
                        sync_result = await sync_service.sync_user_and_subscription(
                            telegram_id=m.from_user.id,
                            tg_username=m.from_user.username,
                            tg_first_name=m.from_user.first_name,
                            tg_last_name=m.from_user.last_name,
                            use_fallback=True,
                            use_cache=False,
                            force_sync=True
                        )
                else:
                    # Нет времени в кэше - делаем синхронизацию
                    sync_result = await sync_service.sync_user_and_subscription(
                        telegram_id=m.from_user.id,
                        tg_username=m.from_user.username,
                        tg_first_name=m.from_user.first_name,
                        tg_last_name=m.from_user.last_name,
                        use_fallback=True,
                        use_cache=False,
                        force_sync=True
                    )
            else:
                # Кэша нет - делаем синхронизацию
                sync_result = await sync_service.sync_user_and_subscription(
                    telegram_id=m.from_user.id,
                    tg_username=m.from_user.username,
                    tg_first_name=m.from_user.first_name,
                    tg_last_name=m.from_user.last_name,
                    use_fallback=True,
                    use_cache=False,
                    force_sync=True
                )
        except RemnaUnavailableError:
            # RemnaWave — единственный источник истины. Не используем local DB fallback.
            logger.warning(f"Remna недоступна для {m.from_user.id}, статус: none")
            if not sync_result:
                sync_result = SyncResult(
                    is_new_user_created=False,
                    user_remna_uuid=None,
                    subscription_status="none",
                    expires_at=None,
                    source="remna_unavailable",
                )
        except Exception as e:
            logger.error(
                f"Ошибка синхронизации для пользователя {m.from_user.id}: {e}\n"
                f"Traceback: {traceback.format_exc()}"
            )
            sync_result = SyncResult(
                is_new_user_created=False,
                user_remna_uuid=None,
                subscription_status="none",
                expires_at=None,
                source="remna_error",
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
    
    # Deep link payment_success отключён — платежи обрабатываются вручную администратором
        
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

    sync_result = None
    remna_success = False
    remna_start_time = time.time()

    try:
        sync_result = await sync_service.sync_user_and_subscription(
            telegram_id=telegram_id,
            tg_username=callback.from_user.username,
            tg_first_name=callback.from_user.first_name,
            tg_last_name=callback.from_user.last_name,
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


async def _handle_solokhin_promo(message: types.Message) -> bool:
    """
    Обрабатывает промокод Solokhin: отправляет заявку админам на Premium 10 дней.
    Возвращает True если заявка отправлена.

    Проверки:
    - Активная подписка → отказ
    """
    from app.services.payment_request import generate_req_id, build_payreq_block
    from app.services.jsonl_logger import log_payment_event

    user_id = message.from_user.id
    promo_code = "solokhin"

    # 1. Проверяем активную подписку
    try:
        sync_service = SyncService()
        sync_result = await sync_service.sync_user_and_subscription(
            telegram_id=user_id,
            tg_username=message.from_user.username,
            tg_first_name=message.from_user.first_name,
            tg_last_name=message.from_user.last_name,
            use_fallback=False,
            use_cache=True,
        )
        if sync_result.subscription_status == "active":
            await message.answer(
                "❌ У вас уже есть активная подписка. Промокод недоступен.",
                reply_markup=get_main_menu_keyboard(user_id=user_id),
            )
            return True  # Обработано, но не выдано
    except RemnaUnavailableError:
        await message.answer(
            "❌ Не удалось проверить статус подписки. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=user_id),
        )
        return True
    except Exception as e:
        logger.error(f"/solokhin: ошибка проверки подписки для {user_id}: {e}")
        await message.answer(
            "❌ Произошла ошибка при проверке подписки. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=user_id),
        )
        return True

    # 2. Создаём заявку и отправляем админам
    req_id = generate_req_id()

    username = f"@{message.from_user.username}" if message.from_user.username else f"ID:{user_id}"
    name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or username
    tariff = "solokhin_10d"

    try:
        payreq_block = build_payreq_block(
            req_id=req_id,
            tg_id=user_id,
            username=username,
            name=name,
            tariff=tariff,
            amount=0,  # Промо — бесплатно
            currency="PROMO",
        )
    except Exception as e:
        logger.error(f"/solokhin: ошибка build_payreq_block: {e}")
        await message.answer(
            "❌ Ошибка при создании заявки. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=user_id),
        )
        return True

    # Логируем
    try:
        log_payment_event(
            event="promo_request_created",
            req_id=req_id,
            tg_id=user_id,
            payload={"promo_code": promo_code, "tariff": tariff, "username": username},
        )
    except Exception as e:
        logger.error(f"/solokhin: ошибка логирования: {e}")

    # Сообщение пользователю
    await message.answer(
        "✅ <b>Заявка на промокод отправлена администратору.</b>\n\n"
        "После подтверждения вам будет выдан Premium на 10 дней.",
        reply_markup=get_main_menu_keyboard(user_id=user_id),
    )

    # Сообщение админам
    admin_msg = (
        f"🎁 <b>ПРОМОКОД SOLOKHIN</b>\n\n"
        f"👤 <b>Пользователь:</b> {username}\n"
        f"🆔 <b>Telegram ID:</b> {user_id}\n"
        f"📝 <b>Имя:</b> {name}\n\n"
        f"📦 <b>Тариф:</b> Premium 10 дней\n"
        f"🎫 <b>Промокод:</b> solokhin\n\n"
        f"<pre>{payreq_block}</pre>"
    )
    admin_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Выдать Premium", callback_data=f"promo_grant_{promo_code}_{user_id}_{req_id}")],
        [types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"promo_reject_{promo_code}_{user_id}_{req_id}")],
        [types.InlineKeyboardButton(text="📩 Написать пользователю", url=f"tg://user?id={user_id}")],
    ])

    for admin_id in (settings.ADMINS or []):
        if isinstance(admin_id, int):
            try:
                await message.bot.send_message(
                    chat_id=admin_id,
                    text=admin_msg,
                    reply_markup=admin_keyboard,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Ошибка отправки промо-заявки админу {admin_id}: {e}")

    return True


@router.message(Command("solokhin"))
async def cmd_solokhin(message: types.Message):
    """Промокод Solokhin — отправляет заявку админам на Premium 10 дней (только slash-команда)."""
    if not getattr(settings, "PROMO_SOLOKHIN_ENABLED", True):
        return
    if await _handle_solokhin_promo(message):
        return
    await message.answer(
        "❌ Не удалось обработать промокод. Попробуйте позже или напишите администратору.",
        reply_markup=get_main_menu_keyboard(user_id=message.from_user.id),
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
    """Команда /friend - уведомление администратору о запросе доступа"""
    user_id = message.from_user.id
    logger.info(f"Пользователь {user_id} вызвал команду /friend")
    try:
        sync_service = SyncService()
        sync_result = await sync_service.sync_user_and_subscription(
            telegram_id=user_id,
            tg_username=message.from_user.username,
            tg_first_name=message.from_user.first_name,
            tg_last_name=message.from_user.last_name,
            use_fallback=False,
            use_cache=False,
            force_sync=True,
            force_remna=True,
        )
        if sync_result.subscription_status == "active":
            await message.answer(
                "❌ У вас уже есть активная подписка.",
                reply_markup=get_main_menu_keyboard(user_id=user_id)
            )
            return
    except RemnaUnavailableError:
        await message.answer(
            "❌ Не удалось проверить статус подписки. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=user_id)
        )
        return
    except Exception as e:
        logger.error(f"Ошибка /friend для {user_id}: {e}")
        await message.answer(
            "❌ Ошибка. Попробуйте позже.",
            reply_markup=get_main_menu_keyboard(user_id=user_id)
        )
        return

    name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".strip() or message.from_user.username or f"User_{user_id}"
    admin_msg = (
        f"👤 <b>Запрос на доступ (/friend)</b>\n\n"
        f"Имя: {name}\n"
        f"Username: @{message.from_user.username or 'не указан'}\n"
        f"Telegram ID: <code>{user_id}</code>\n\n"
        f"Выдайте Premium или отклоните запрос."
    )
    admin_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Выдать Premium на 1 месяц", callback_data=f"friend_grant_1m_{user_id}")],
        [types.InlineKeyboardButton(text="Выдать Premium на 3 месяца", callback_data=f"friend_grant_3m_{user_id}")],
        [types.InlineKeyboardButton(text="Выдать Premium навсегда", callback_data=f"friend_grant_forever_{user_id}")],
        [types.InlineKeyboardButton(text="Отклонить", callback_data=f"friend_reject_{user_id}")],
        [types.InlineKeyboardButton(text="📩 Написать пользователю", url=f"tg://user?id={user_id}")],
    ])
    for admin_id in settings.ADMINS:
        try:
            await message.bot.send_message(
                chat_id=admin_id,
                text=admin_msg,
                reply_markup=admin_keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки /friend админу {admin_id}: {e}")
    await message.answer(
        "⏳ Запрос отправлен администратору. Ожидайте ответа.",
        reply_markup=get_main_menu_keyboard(user_id=user_id)
    )


@router.callback_query(lambda c: c.data == "friend_request_yes")
async def friend_request_yes(callback: types.CallbackQuery):
    """Устаревший handler — /friend теперь без подтверждения."""
    await callback.answer("Используйте команду /friend", show_alert=True)


@router.callback_query(lambda c: c.data == "friend_request_no")
async def friend_request_no(callback: types.CallbackQuery):
    """Обработчик кнопки 'Нет' для запроса на доступ"""
    await callback.answer()
    await callback.message.edit_text("Запрос отменён", reply_markup=None)

