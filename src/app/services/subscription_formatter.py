"""Модуль для форматирования информации о подписке"""
from datetime import datetime
from typing import Optional, Tuple


def format_subscription_time(valid_until: Optional[datetime]) -> Tuple[str, bool]:
    """
    Форматирует время подписки в едином формате
    
    Returns:
        Tuple[str, bool]: (текст о подписке, has_subscription)
    """
    if not valid_until:
        return "💳 Подписка:\n• Подписка не активна", False
    
    now = datetime.utcnow()
    valid_until_str = valid_until.strftime("%d.%m.%Y")
    
    # Вычисляем оставшееся время
    time_diff = valid_until - now
    total_seconds = int(time_diff.total_seconds())
    
    if total_seconds <= 0:
        return "💳 Подписка:\n• Действует до: {}\n• Осталось: истекла".format(valid_until_str), True
    
    days_left = time_diff.days
    
    subscription_text = "💳 Подписка:\n"
    subscription_text += "• Действует до: {}\n".format(valid_until_str)
    subscription_text += "• Осталось (дней): {}".format(days_left)
    
    return subscription_text, True


def format_subscription_info(subscription) -> Tuple[str, bool]:
    """
    Форматирует информацию о подписке
    
    Args:
        subscription: Объект подписки или None
        
    Returns:
        Tuple[str, bool]: (текст о подписке, has_subscription)
    """
    if not subscription:
        return "💳 Подписка:\n• Подписка не активна", False
    
    if subscription.valid_until:
        return format_subscription_time(subscription.valid_until)
    else:
        return "💳 Подписка:\n• Действует до: без ограничений", True

