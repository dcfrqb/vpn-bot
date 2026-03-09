"""
Единый router для UI callbacks
Обрабатывает все callback_data с префиксом "ui:"
"""
from aiogram import Router, types, F
from app.logger import logger
from app.ui.screen_manager import get_screen_manager
from app.ui.callbacks import parse_cb, is_ui_callback, CallbackParseError
from app.ui.screens import ScreenID

router = Router(name="ui")


@router.callback_query(F.data.startswith("ui:"))
async def ui_callback_handler(callback: types.CallbackQuery):
    """
    Единый обработчик для всех UI callbacks
    
    Формат: ui:{screen}:{action}:{payload}
    
    ОПТИМИЗАЦИЯ: answerCallbackQuery вызывается МГНОВЕННО до всех тяжелых операций
    """
    import time
    user_id = callback.from_user.id
    request_start = time.monotonic()
    
    # КРИТИЧНО: Отвечаем МГНОВЕННО, до парсинга и обработки
    # Это дает пользователю мгновенный визуальный отклик
    try:
        await callback.answer()
        answer_duration = (time.monotonic() - request_start) * 1000
        logger.debug(f"[PERF] callback.answer() duration={answer_duration:.2f}ms, user_id={user_id}")
    except Exception as e:
        logger.warning(f"Ошибка при answerCallbackQuery: {e}")
        # Продолжаем обработку даже если answer не удался
    
    _uname = f"@{callback.from_user.username}" if callback.from_user.username else "no_username"
    logger.info(f"UI callback от пользователя {user_id} ({_uname}): {callback.data}")
    
    try:
        # Парсим callback_data (легкая операция)
        parse_start = time.monotonic()
        parsed = parse_cb(callback.data)
        parse_duration = (time.monotonic() - parse_start) * 1000
        
        if not parsed:
            logger.warning(f"Не удалось распарсить callback_data: {callback.data}")
            await callback.answer("❌ Ошибка обработки запроса", show_alert=True)
            return
        
        screen_id, action, payload = parsed
        
        # Обрабатываем действие через ScreenManager (может быть тяжелой операцией)
        # Защита от гонок: блокируем обработку для одного пользователя
        screen_manager = get_screen_manager()
        user_lock = screen_manager._get_user_lock(user_id)
        
        async with user_lock:
            action_start = time.monotonic()
            success = await screen_manager.handle_action(
                screen_id=screen_id,
                action=action,
                payload=payload,
                message_or_callback=callback,
                user_id=user_id
            )
            action_duration = (time.monotonic() - action_start) * 1000
            total_duration = (time.monotonic() - request_start) * 1000
            
            logger.info(
                f"[PERF] callback={screen_id.value}.{action} total={total_duration:.2f}ms "
                f"parse={parse_duration:.2f}ms action={action_duration:.2f}ms user_id={user_id}"
            )
        
        if not success:
            logger.warning(
                f"Действие не обработано: screen={screen_id}, action={action}, payload={payload}, user_id={user_id}"
            )
            # Не вызываем callback.answer() повторно - уже вызвали в начале
    
    except CallbackParseError as e:
        logger.error(f"Ошибка парсинга callback_data: {e}")
        await callback.answer("❌ Ошибка формата запроса", show_alert=True)
    except Exception as e:
        logger.exception(f"Ошибка при обработке UI callback: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)