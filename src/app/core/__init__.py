"""
Core модули: конфигурация, ошибки, утилиты
"""
from app.core.pagination import Pagination
from app.core.errors import (
    AppError,
    RemnaUnavailableError,
    NavigationError,
    ValidationError,
    RepositoryError,
    ServiceError
)

__all__ = [
    "Pagination",
    "AppError",
    "RemnaUnavailableError",
    "NavigationError",
    "ValidationError",
    "RepositoryError",
    "ServiceError",
]
