"""
Типы действий для экранов
"""
from enum import Enum


class ActionType(Enum):
    """Тип действия на экране"""
    NAVIGATION = "navigation"  # Переход между экранами (open, back)
    STATE = "state"  # Изменение состояния экрана (refresh, page, filter, select)
    FLOW = "flow"  # Временный переход (help)


def get_action_type(action: str, screen_id: str = None) -> ActionType:
    """
    Определяет тип действия
    
    Args:
        action: Название действия
        screen_id: ID экрана (опционально, для специфичных случаев)
        
    Returns:
        ActionType
    """
    # FLOW actions - проверяем сначала, так как они могут иметь action="open"
    if screen_id == "help" and action == "open":
        return ActionType.FLOW
    if action == "help":
        return ActionType.FLOW
    
    # NAVIGATION actions
    if action in ("open", "back"):
        return ActionType.NAVIGATION
    
    # STATE actions
    if action in ("refresh", "page", "filter", "select"):
        return ActionType.STATE
    
    # По умолчанию - STATE (для неизвестных действий)
    return ActionType.STATE
