"""
Обработка legacy callback_data для обратной совместимости
Преобразует старые форматы в новый формат ui: и вызывает ScreenManager

TODO: Удалить после полной миграции всех callbacks
"""
from aiogram import Router, types, F
from app.logger import logger
from app.ui.screen_manager import get_screen_manager
from app.ui.screens import ScreenID
from app.ui.callbacks import is_ui_callback

router = Router(name="legacy_callbacks")


# Маппинг старых callback_data на новые форматы
LEGACY_CALLBACK_MAP = {
    "back_to_main": (ScreenID.MAIN_MENU, "back", "-"),
    "buy_subscription": (ScreenID.SUBSCRIPTION_PLANS, "open", "-"),
    "connect_vpn": (ScreenID.CONNECT, "open", "-"),
    "help": (ScreenID.HELP, "open", "-"),
    "refresh_info": (ScreenID.MAIN_MENU, "refresh", "-"),
    "admin_panel": (ScreenID.ADMIN_PANEL, "open", "-"),
    "admin_stats": (ScreenID.ADMIN_STATS, "open", "-"),
    "admin_users": (ScreenID.ADMIN_USERS, "open", "-"),
    "admin_payments": (ScreenID.ADMIN_PAYMENTS, "open", "-"),
    "admin_back": (ScreenID.ADMIN_PANEL, "back", "-"),
}


def convert_legacy_callback(callback_data: str) -> tuple[ScreenID, str, str] | None:
    """
    Преобразует legacy callback_data в новый формат
    
    Args:
        callback_data: Старый формат callback_data
        
    Returns:
        Tuple (screen_id, action, payload) или None, если не удалось преобразовать
    """
    # Прямые маппинги
    if callback_data in LEGACY_CALLBACK_MAP:
        return LEGACY_CALLBACK_MAP[callback_data]
    
    # Специальные форматы
    if callback_data.startswith("plan_basic"):
        # plan_basic -> выбор базового тарифа
        if callback_data == "plan_basic":
            return (ScreenID.SUBSCRIPTION_PLAN_DETAIL, "select", "basic")
        # plan_basic_1, plan_basic_3 и т.д. -> выбор периода (legacy, обрабатывается в start.py)
        return None
    
    if callback_data.startswith("plan_premium"):
        if callback_data == "plan_premium":
            return (ScreenID.SUBSCRIPTION_PLAN_DETAIL, "select", "premium")
        return None
    
    if callback_data.startswith("admin_users_page_"):
        # admin_users_page_2 -> page action
        try:
            page = int(callback_data.split("_")[-1])
            return (ScreenID.ADMIN_USERS, "page", str(page))
        except ValueError:
            return None
    
    if callback_data.startswith("admin_payments_"):
        # admin_payments_all, admin_payments_succeeded и т.д.
        parts = callback_data.split("_")
        if len(parts) >= 3:
            if parts[2] == "page":
                # admin_payments_page_2_all -> page action
                try:
                    page = int(parts[3]) if len(parts) > 3 else 1
                    filter_str = parts[4] if len(parts) > 4 else "all"
                    return (ScreenID.ADMIN_PAYMENTS, "page", f"{page}&{filter_str}")
                except (ValueError, IndexError):
                    return None
            else:
                # admin_payments_all, admin_payments_succeeded -> filter action
                filter_type = parts[2]
                return (ScreenID.ADMIN_PAYMENTS, "filter", filter_type)
        return None
    
    return None


@router.callback_query()
async def legacy_callback_handler(callback: types.CallbackQuery):
    """
    Обработчик legacy callbacks - преобразует в новый формат и вызывает ScreenManager
    
    ВАЖНО: Этот handler должен быть последним в цепочке обработчиков,
    чтобы не перехватывать ui: callbacks и другие специфичные обработчики
    """
    # Пропускаем ui: callbacks (они обрабатываются в ui.py)
    if is_ui_callback(callback.data):
        return
    
    # Пропускаем payment callbacks (обрабатываются в payments.py)
    if callback.data.startswith("pay_"):
        return
    
    # Пропускаем crypto payment callbacks
    if callback.data.startswith("crypto_") or callback.data.startswith("change_payment_"):
        return
    
    # Пропускаем friend request callbacks
    if callback.data.startswith("friend_request_"):
        return
    
    # Пропускаем admin grant/reject callbacks (обрабатываются в admin.py)
    if callback.data.startswith("admin_grant_") or callback.data.startswith("admin_reject_"):
        return
    
    user_id = callback.from_user.id
    logger.info(f"Legacy callback от пользователя {user_id}: {callback.data}")
    
    # Пытаемся преобразовать в новый формат
    converted = convert_legacy_callback(callback.data)
    
    if not converted:
        # Неизвестный legacy callback - логируем и игнорируем
        logger.warning(f"Неизвестный legacy callback: {callback.data}")
        await callback.answer("❌ Устаревший формат запроса", show_alert=True)
        return
    
    screen_id, action, payload = converted
    
    try:
        await callback.answer()
        
        # Обрабатываем через ScreenManager
        screen_manager = get_screen_manager()
        
        # ВАЖНО: Для действия "back" используем текущий экран из Navigator, а не из маппинга
        # Это позволяет правильно обрабатывать "back" с любого экрана
        if action == "back" and screen_id == ScreenID.MAIN_MENU:
            # Для legacy "back_to_main" используем текущий экран из Navigator
            from app.navigation.navigator import get_navigator
            navigator = get_navigator()
            current_screen = navigator.get_current_screen(user_id)
            if current_screen:
                screen_id = current_screen
            else:
                # Если текущего экрана нет, используем MAIN_MENU как fallback
                screen_id = ScreenID.MAIN_MENU
        
        success = await screen_manager.handle_action(
            screen_id=screen_id,
            action=action,
            payload=payload,
            message_or_callback=callback,
            user_id=user_id
        )
        
        if not success:
            logger.warning(
                f"Legacy callback не обработан: {callback.data} -> {screen_id}:{action}:{payload}"
            )
    
    except Exception as e:
        logger.exception(f"Ошибка при обработке legacy callback {callback.data}: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)