"""
Исключения для приложения
"""
from typing import Optional


class AppError(Exception):
    """Базовое исключение приложения"""
    pass


class RemnaUnavailableError(AppError):
    """Ошибка недоступности Remna API"""
    def __init__(self, message: str = "Remna API недоступна", details: Optional[str] = None):
        super().__init__(message)
        self.details = details


class NavigationError(AppError):
    """Ошибка навигации"""
    def __init__(self, message: str, from_screen: Optional[str] = None, to_screen: Optional[str] = None):
        super().__init__(message)
        self.from_screen = from_screen
        self.to_screen = to_screen


class ValidationError(AppError):
    """Ошибка валидации данных"""
    pass


class RepositoryError(AppError):
    """Ошибка репозитория"""
    pass


class ServiceError(AppError):
    """Ошибка сервиса"""
    pass


class InfraError(AppError):
    """
    Ошибка инфраструктуры (внешние API, сеть, таймауты)
    
    Используется для ошибок внешних интеграций:
    - Remna API
    - Платежные системы (YooKassa, криптоплатежи)
    - Сетевые ошибки
    - Таймауты
    """
    def __init__(self, message: str, service: Optional[str] = None, details: Optional[str] = None):
        super().__init__(message)
        self.service = service  # Название сервиса (remna, yookassa, crypto и т.д.)
        self.details = details
