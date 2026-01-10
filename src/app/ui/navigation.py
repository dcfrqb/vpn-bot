"""
Таблица переходов между экранами с enforce правил
Определяет, какие переходы разрешены и какие параметры требуются
"""
from typing import Dict, Set, Optional, Literal
from app.ui.screens import ScreenID
from app.logger import logger

# Тип роли пользователя
UserRole = Literal["user", "admin"]


# Админские экраны (требуют прав администратора)
ADMIN_SCREENS: Set[ScreenID] = {
    ScreenID.ADMIN_PANEL,
    ScreenID.ADMIN_STATS,
    ScreenID.ADMIN_USERS,
    ScreenID.ADMIN_PAYMENTS,
    ScreenID.ADMIN_GRANTS
}

# Таблица переходов: from_screen -> set of allowed target screens
NAVIGATION_MAP: Dict[ScreenID, Set[ScreenID]] = {
    ScreenID.MAIN_MENU: {
        ScreenID.SUBSCRIPTION_PLANS,
        ScreenID.CONNECT,
        ScreenID.HELP,
        ScreenID.ADMIN_PANEL,
        ScreenID.PROFILE
    },
    ScreenID.SUBSCRIPTION_PLANS: {
        ScreenID.SUBSCRIPTION_PLAN_DETAIL,
        ScreenID.MAIN_MENU,
        ScreenID.HELP  # Можно открыть помощь с экрана выбора тарифов
    },
    ScreenID.SUBSCRIPTION_PLAN_DETAIL: {
        ScreenID.SUBSCRIPTION_PAYMENT,
        ScreenID.SUBSCRIPTION_PLANS,
        ScreenID.HELP  # Можно открыть помощь с экрана деталей тарифа
    },
    ScreenID.SUBSCRIPTION_PAYMENT: {
        ScreenID.SUBSCRIPTION_PLANS,
        ScreenID.MAIN_MENU,
        ScreenID.HELP  # Можно открыть помощь с экрана оплаты
    },
    ScreenID.CONNECT: {
        ScreenID.MAIN_MENU
    },
    ScreenID.CONNECT_SUCCESS: {  # DEPRECATED
        ScreenID.MAIN_MENU
    },
    ScreenID.HELP: {
        ScreenID.MAIN_MENU,
        ScreenID.SUBSCRIPTION_PAYMENT,  # Можно вернуться на экран оплаты
        ScreenID.SUBSCRIPTION_PLAN_DETAIL  # Можно вернуться к деталям тарифа
    },
    ScreenID.ADMIN_PANEL: {
        ScreenID.ADMIN_STATS,
        ScreenID.ADMIN_USERS,
        ScreenID.ADMIN_PAYMENTS,
        ScreenID.MAIN_MENU
    },
    ScreenID.ADMIN_STATS: {
        ScreenID.ADMIN_PANEL
    },
    ScreenID.ADMIN_USERS: {
        ScreenID.ADMIN_PANEL
    },
    ScreenID.ADMIN_PAYMENTS: {
        ScreenID.ADMIN_PANEL
    },
    ScreenID.PROFILE: {
        ScreenID.MAIN_MENU
    },
    # ERROR экраны доступны из любого места
    ScreenID.ERROR: {
        ScreenID.MAIN_MENU
    },
    ScreenID.ACCESS_DENIED: {
        ScreenID.MAIN_MENU
    },
    ScreenID.REMNA_UNAVAILABLE: {
        ScreenID.MAIN_MENU
    }
}


def can_navigate(
    from_screen: ScreenID,
    to_screen: ScreenID,
    role: UserRole = "user"
) -> bool:
    """
    Проверяет, разрешен ли переход между экранами с учетом роли
    
    Args:
        from_screen: Исходный экран
        to_screen: Целевой экран
        role: Роль пользователя ("user" или "admin")
        
    Returns:
        True, если переход разрешен
    """
    # Проверка прав доступа к админским экранам
    if to_screen in ADMIN_SCREENS and role != "admin":
        logger.warning(
            f"Попытка перехода в админский экран {to_screen} пользователем с ролью {role}"
        )
        return False
    
    # Проверка правил навигации
    allowed_targets = NAVIGATION_MAP.get(from_screen, set())
    
    # ERROR экраны доступны из любого места
    if to_screen in {ScreenID.ERROR, ScreenID.ACCESS_DENIED, ScreenID.REMNA_UNAVAILABLE}:
        return True
    
    return to_screen in allowed_targets


def get_allowed_navigations(from_screen: ScreenID, role: UserRole = "user") -> Set[ScreenID]:
    """
    Возвращает множество экранов, на которые можно перейти с текущего
    
    Args:
        from_screen: Исходный экран
        role: Роль пользователя
        
    Returns:
        Множество разрешенных целевых экранов (с учетом роли)
    """
    allowed = NAVIGATION_MAP.get(from_screen, set())
    
    # Фильтруем админские экраны для не-админов
    if role != "admin":
        allowed = {screen for screen in allowed if screen not in ADMIN_SCREENS}
    
    return allowed