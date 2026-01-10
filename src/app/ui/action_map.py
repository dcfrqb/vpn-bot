"""
Таблица действий для экранов - явное определение ACTION → EFFECT
"""
from typing import Dict, Set, Optional, Literal, Tuple
from app.ui.screens import ScreenID
from app.ui.action_types import ActionType

# Тип эффекта действия
ActionEffect = Literal["NAVIGATION", "STATE", "FLOW"]

# Структура: screen_id -> action -> (action_type, target_screen | None)
ACTION_MAP: Dict[ScreenID, Dict[str, Tuple[ActionEffect, Optional[ScreenID]]]] = {
    ScreenID.MAIN_MENU: {
        "open": ("NAVIGATION", None),  # Открытие самого себя
        "refresh": ("STATE", None),
        "back": ("STATE", None),  # Back в main_menu просто обновляет экран (игнорируется)
    },
    ScreenID.CONNECT: {
        "open": ("FLOW", None),  # FLOW, так как требует async обработку (loading → success/error)
        "back": ("NAVIGATION", ScreenID.MAIN_MENU),
        "refresh": ("STATE", None),
    },
    ScreenID.SUBSCRIPTION_PLANS: {
        "open": ("NAVIGATION", None),
        "select": ("NAVIGATION", ScreenID.SUBSCRIPTION_PLAN_DETAIL),  # Выбор тарифа -> переход на детальный экран
        "back": ("NAVIGATION", ScreenID.MAIN_MENU),
    },
    ScreenID.SUBSCRIPTION_PLAN_DETAIL: {
        "open": ("NAVIGATION", None),
        "select": ("NAVIGATION", None),  # Остаётся на том же экране, но меняет состояние
        "back": ("NAVIGATION", ScreenID.SUBSCRIPTION_PLANS),
    },
    ScreenID.SUBSCRIPTION_PAYMENT: {
        "open": ("NAVIGATION", None),
        "back": ("NAVIGATION", ScreenID.SUBSCRIPTION_PLAN_DETAIL),
    },
    ScreenID.HELP: {
        "open": ("FLOW", None),
        "back": ("NAVIGATION", None),  # Назад через Navigator (вернется на предыдущий экран из backstack)
    },
    ScreenID.ADMIN_PANEL: {
        "open": ("NAVIGATION", None),
        "refresh": ("STATE", None),
        "users": ("NAVIGATION", ScreenID.ADMIN_USERS),
        "payments": ("NAVIGATION", ScreenID.ADMIN_PAYMENTS),
        "back": ("NAVIGATION", ScreenID.MAIN_MENU),
    },
    ScreenID.ADMIN_STATS: {
        "open": ("NAVIGATION", None),
        "refresh": ("STATE", None),
        "back": ("NAVIGATION", ScreenID.ADMIN_PANEL),
    },
    ScreenID.ADMIN_USERS: {
        "open": ("NAVIGATION", None),
        "page": ("STATE", None),
        "back": ("NAVIGATION", ScreenID.ADMIN_PANEL),
    },
    ScreenID.ADMIN_PAYMENTS: {
        "open": ("NAVIGATION", None),
        "page": ("STATE", None),
        "filter": ("STATE", None),
        "back": ("NAVIGATION", ScreenID.ADMIN_PANEL),
    },
    ScreenID.PROFILE: {
        "open": ("NAVIGATION", None),
        "back": ("NAVIGATION", ScreenID.MAIN_MENU),
    },
    ScreenID.ERROR: {
        "open": ("NAVIGATION", None),
        "back": ("NAVIGATION", ScreenID.MAIN_MENU),
    },
    ScreenID.ACCESS_DENIED: {
        "open": ("NAVIGATION", None),
        "back": ("NAVIGATION", ScreenID.MAIN_MENU),
    },
}


def get_action_effect(screen_id: ScreenID, action: str) -> Optional[Tuple[ActionEffect, Optional[ScreenID]]]:
    """
    Получает эффект действия для экрана
    
    Args:
        screen_id: ID экрана
        action: Действие
        
    Returns:
        Tuple[ActionEffect, Optional[ScreenID]] или None, если действие не определено
    """
    screen_actions = ACTION_MAP.get(screen_id)
    if not screen_actions:
        return None
    
    return screen_actions.get(action)


def is_action_allowed(screen_id: ScreenID, action: str) -> bool:
    """
    Проверяет, разрешено ли действие для экрана
    
    Args:
        screen_id: ID экрана
        action: Действие
        
    Returns:
        True, если действие разрешено
    """
    return get_action_effect(screen_id, action) is not None
