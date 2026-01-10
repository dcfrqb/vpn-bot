"""
Утилиты для работы с единым форматом callback_data

Формат: ui:{screen}:{action}:{payload}
- screen: ScreenID (строкой)
- action: строка (open, back, refresh, select_plan, page, filter, grant, reject и т.д.)
- payload: опционально, компактная строка (макс 30 символов для безопасности)
"""
from typing import Optional, Tuple
from app.ui.screens import ScreenID
from app.logger import logger


# Префикс для UI callbacks
UI_PREFIX = "ui:"

# Максимальная длина callback_data в Telegram (64 байта)
MAX_CALLBACK_LENGTH = 64

# Максимальная длина payload (с учетом префикса и разделителей)
MAX_PAYLOAD_LENGTH = 30


class CallbackParseError(Exception):
    """Ошибка парсинга callback_data"""
    pass


def build_cb(screen: ScreenID, action: str, payload: str = "-") -> str:
    """
    Строит callback_data в едином формате
    
    Args:
        screen: ScreenID экрана
        action: Действие (open, back, refresh, page, filter и т.д.)
        payload: Дополнительные данные (опционально, по умолчанию "-")
        
    Returns:
        Строка формата "ui:{screen}:{action}:{payload}"
        
    Raises:
        ValueError: Если payload слишком длинный или содержит недопустимые символы
    """
    # Валидация payload
    if len(payload) > MAX_PAYLOAD_LENGTH:
        raise ValueError(f"Payload слишком длинный (макс {MAX_PAYLOAD_LENGTH} символов): {payload}")
    
    # Проверяем, что payload не содержит разделителей
    if ":" in payload:
        raise ValueError(f"Payload не может содержать ':' символ: {payload}")
    
    # Нормализуем screen ID (берем value из enum)
    screen_str = screen.value
    
    # Строим callback_data
    callback_data = f"{UI_PREFIX}{screen_str}:{action}:{payload}"
    
    # Проверяем общую длину
    if len(callback_data) > MAX_CALLBACK_LENGTH:
        raise ValueError(
            f"Callback data слишком длинный ({len(callback_data)} > {MAX_CALLBACK_LENGTH}): {callback_data}"
        )
    
    return callback_data


def parse_cb(data: str) -> Optional[Tuple[ScreenID, str, str]]:
    """
    Парсит callback_data из единого формата
    
    Args:
        data: Callback data строка
        
    Returns:
        Tuple (screen_id, action, payload) или None, если формат неверный
        
    Raises:
        CallbackParseError: Если формат неверный или screen_id не найден
    """
    if not data:
        return None
    
    # Проверяем префикс
    if not data.startswith(UI_PREFIX):
        return None
    
    # Убираем префикс
    data_without_prefix = data[len(UI_PREFIX):]
    
    # Разбиваем по разделителю
    parts = data_without_prefix.split(":", 2)
    
    if len(parts) < 2:
        logger.warning(f"Неверный формат callback_data (недостаточно частей): {data}")
        return None
    
    screen_str = parts[0]
    action = parts[1]
    payload = parts[2] if len(parts) > 2 else "-"
    
    # Пытаемся найти ScreenID
    try:
        screen_id = ScreenID(screen_str)
    except ValueError:
        logger.warning(f"Неизвестный ScreenID в callback_data: {screen_str}")
        raise CallbackParseError(f"Неизвестный ScreenID: {screen_str}")
    
    return (screen_id, action, payload)


def is_ui_callback(data: Optional[str]) -> bool:
    """
    Проверяет, является ли callback_data UI callback
    
    Args:
        data: Callback data строка (может быть None)
        
    Returns:
        True, если это UI callback, False иначе
    """
    if not data:
        return False
    return data.startswith(UI_PREFIX)


def validate_callback_length(callback_data: str) -> bool:
    """
    Проверяет, что callback_data не превышает лимит Telegram
    
    Args:
        callback_data: Callback data строка
        
    Returns:
        True, если длина допустима
    """
    return len(callback_data) <= MAX_CALLBACK_LENGTH