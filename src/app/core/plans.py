"""
Единый справочник тарифов (plan_code -> plan_name).
Используется для нормализации названий тарифов во всех частях бота.
"""
from typing import Optional
from app.logger import logger

# Маппинг plan_code -> человекочитаемое название
PLAN_NAMES: dict[str, str] = {
    "basic": "Базовый тариф",
    "premium": "Премиум тариф",
    "trial": "Пробный период",
    "pro": "Про тариф",
}

# Fallback для неизвестных тарифов (показываем пользователю)
PLAN_NAME_FALLBACK = "Тариф (обновите меню)"

# Список валидных plan_code для проверки
VALID_PLAN_CODES = frozenset(PLAN_NAMES.keys())


def get_plan_name(plan_code: Optional[str]) -> str:
    """
    Возвращает человекочитаемое название тарифа по plan_code.
    
    Args:
        plan_code: Код тарифа (basic, premium, trial и т.д.)
        
    Returns:
        Название тарифа или fallback для неизвестных кодов
    """
    if not plan_code:
        return PLAN_NAME_FALLBACK
    plan_code_lower = str(plan_code).lower().strip()
    name = PLAN_NAMES.get(plan_code_lower)
    if name:
        return name
    logger.warning(
        f"Неизвестный plan_code: {plan_code!r}, "
        f"используем fallback. Добавьте в PLAN_NAMES при необходимости."
    )
    return PLAN_NAME_FALLBACK


def is_valid_plan_code(plan_code: Optional[str]) -> bool:
    """Проверяет, является ли plan_code известным."""
    if not plan_code:
        return False
    return str(plan_code).lower().strip() in VALID_PLAN_CODES
