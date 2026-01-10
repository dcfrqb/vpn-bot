"""
Строгая схема callback_data с enum и payload
Улучшенная версия ui/callbacks.py с типизацией действий
"""
from enum import Enum
from typing import Optional, Tuple
from app.ui.screens import ScreenID
from app.logger import logger


class CallbackAction(Enum):
    """Действия в callback_data"""
    # Навигация
    OPEN = "open"
    BACK = "back"
    REFRESH = "refresh"
    
    # Состояние
    PAGE = "page"
    FILTER = "filter"
    SELECT = "select"
    
    # Специальные
    FLOW = "flow"
    
    # Админские действия (для обратной совместимости)
    GRANT = "grant"
    REJECT = "reject"
    STATS = "stats"
    USERS = "users"
    PAYMENTS = "payments"


class CallbackSchema:
    """Схема для парсинга и генерации callback_data"""
    
    SEPARATOR = ":"
    PREFIX = "ui"
    MAX_CALLBACK_LENGTH = 64  # Лимит Telegram для callback_data
    
    @staticmethod
    def build(
        screen_id: ScreenID,
        action: CallbackAction,
        payload: Optional[str] = None
    ) -> str:
        """
        Строит callback_data в формате: ui:{screen}:{action}:{payload}
        
        Args:
            screen_id: ID экрана
            action: Действие
            payload: Дополнительные данные (опционально)
            
        Returns:
            Строка callback_data
            
        Raises:
            ValueError: Если callback_data превышает 64 байта
        """
        parts = [CallbackSchema.PREFIX, screen_id.value, action.value]
        if payload:
            parts.append(payload)
        callback_data = CallbackSchema.SEPARATOR.join(parts)
        
        # Проверка длины callback_data (64 байта лимит Telegram)
        callback_bytes = callback_data.encode('utf-8')
        if len(callback_bytes) > CallbackSchema.MAX_CALLBACK_LENGTH:
            error_msg = (
                f"Callback data слишком длинный ({len(callback_bytes)} > {CallbackSchema.MAX_CALLBACK_LENGTH} байт): "
                f"{callback_data[:50]}..."
            )
            logger.error(f"[CALLBACK_SCHEMA] {error_msg}")
            raise ValueError(error_msg)
        
        return callback_data
    
    @staticmethod
    def parse(callback_data: str) -> Optional[Tuple[ScreenID, CallbackAction, Optional[str]]]:
        """
        Парсит callback_data
        
        Args:
            callback_data: Строка callback_data
            
        Returns:
            Tuple[ScreenID, CallbackAction, Optional[str]] или None при ошибке
        """
        if not callback_data.startswith(f"{CallbackSchema.PREFIX}{CallbackSchema.SEPARATOR}"):
            return None
        
        try:
            parts = callback_data.split(CallbackSchema.SEPARATOR)
            if len(parts) < 3:
                return None
            
            # parts[0] = "ui", parts[1] = screen_id, parts[2] = action
            screen_id_str = parts[1]
            action_str = parts[2]
            payload = parts[3] if len(parts) > 3 else None
            
            # Парсим ScreenID
            try:
                screen_id = ScreenID(screen_id_str)
            except ValueError:
                logger.warning(f"Неизвестный ScreenID: {screen_id_str}")
                return None
            
            # Парсим Action (пробуем enum, если не получается - возвращаем строку)
            try:
                action = CallbackAction(action_str)
            except ValueError:
                # Для обратной совместимости возвращаем строку, но логируем
                logger.debug(f"Неизвестный CallbackAction (используется строка): {action_str}")
                # Создаем временный enum для обратной совместимости
                # В реальности нужно будет мигрировать все действия на enum
                class TempAction(Enum):
                    CUSTOM = action_str
                action = TempAction.CUSTOM
            
            return (screen_id, action, payload)
            
        except Exception as e:
            logger.error(f"Ошибка парсинга callback_data '{callback_data}': {e}")
            return None
    
    @staticmethod
    def is_ui_callback(callback_data: str) -> bool:
        """Проверяет, является ли callback_data UI callback'ом"""
        return callback_data.startswith(f"{CallbackSchema.PREFIX}{CallbackSchema.SEPARATOR}")


# Обратная совместимость с ui/callbacks.py
def build_cb(screen: ScreenID, action: str, payload: str = "-") -> str:
    """
    Строит callback_data (обратная совместимость)
    
    Args:
        screen: ScreenID экрана
        action: Действие (строка или CallbackAction)
        payload: Дополнительные данные
        
    Returns:
        Строка callback_data
        
    Raises:
        ValueError: Если callback_data превышает 64 байта
    """
    # Пытаемся распарсить action как enum
    try:
        if isinstance(action, CallbackAction):
            action_enum = action
        else:
            action_enum = CallbackAction(action)
    except ValueError:
        # Если не enum, используем как строку (для обратной совместимости)
        # Но это не идеально, лучше мигрировать все на enum
        parts = [CallbackSchema.PREFIX, screen.value, action]
        if payload and payload != "-":
            parts.append(payload)
        callback_data = CallbackSchema.SEPARATOR.join(parts)
        
        # Проверка длины
        callback_bytes = callback_data.encode('utf-8')
        if len(callback_bytes) > CallbackSchema.MAX_CALLBACK_LENGTH:
            error_msg = (
                f"Callback data слишком длинный ({len(callback_bytes)} > {CallbackSchema.MAX_CALLBACK_LENGTH} байт): "
                f"{callback_data[:50]}..."
            )
            logger.error(f"[BUILD_CB] {error_msg}")
            raise ValueError(error_msg)
        
        return callback_data
    
    return CallbackSchema.build(screen, action_enum, payload if payload != "-" else None)


def parse_cb(data: str) -> Optional[Tuple[ScreenID, str, str]]:
    """
    Парсит callback_data (обратная совместимость)
    
    Returns:
        Tuple[ScreenID, str, str] - (screen_id, action_str, payload)
    """
    result = CallbackSchema.parse(data)
    if not result:
        return None
    
    screen_id, action, payload = result
    
    # Преобразуем action в строку для обратной совместимости
    if isinstance(action, CallbackAction):
        action_str = action.value
    else:
        action_str = str(action.value) if hasattr(action, 'value') else str(action)
    
    return (screen_id, action_str, payload or "-")


def is_ui_callback(callback_data: str) -> bool:
    """Проверяет, является ли callback_data UI callback'ом"""
    return CallbackSchema.is_ui_callback(callback_data)
