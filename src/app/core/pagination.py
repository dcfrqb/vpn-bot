"""
Единый объект пагинации с сохранением состояния
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any
import json


@dataclass
class Pagination:
    """Единый объект пагинации с сохранением состояния"""
    page: int = 1
    page_size: int = 10
    total: int = 0
    
    def __post_init__(self):
        """Валидация после инициализации"""
        if self.page < 1:
            self.page = 1
        if self.page_size < 1:
            self.page_size = 10
        if self.total < 0:
            self.total = 0
    
    @property
    def total_pages(self) -> int:
        """Вычисляет общее количество страниц"""
        if self.total == 0:
            return 1
        return (self.total + self.page_size - 1) // self.page_size
    
    @property
    def has_next(self) -> bool:
        """Есть ли следующая страница"""
        return self.page < self.total_pages
    
    @property
    def has_prev(self) -> bool:
        """Есть ли предыдущая страница"""
        return self.page > 1
    
    def next_page(self) -> Optional[int]:
        """Возвращает номер следующей страницы или None"""
        if self.has_next:
            return self.page + 1
        return None
    
    def prev_page(self) -> Optional[int]:
        """Возвращает номер предыдущей страницы или None"""
        if self.has_prev:
            return self.page - 1
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Сериализация для хранения в callback_data
        
        ВАЖНО: total НЕ передается в callback_data (вычисляется на сервере)
        Используются сжатые ключи для экономии места (64 байта лимит Telegram)
        """
        return {
            "p": self.page,      # page → p
            "s": self.page_size  # page_size → s
            # total НЕ передается - вычисляется на сервере
        }
    
    def to_payload(self) -> str:
        """
        Сериализация в строку для callback_data
        Использует компактный формат без JSON (без двоеточий): p{page}s{page_size}
        Пример: p2s10 (страница 2, размер 10)
        """
        return f"p{self.page}s{self.page_size}"
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Pagination":
        """
        Десериализация из словаря
        
        Поддерживает как старый формат (page, page_size) так и новый (p, s)
        total НЕ берется из payload - должен быть установлен отдельно через update_total()
        """
        # Поддержка сжатых ключей (p, s) и старых (page, page_size)
        page = data.get("p") or data.get("page", 1)
        page_size = data.get("s") or data.get("page_size", 10)
        # total НЕ берем из payload - вычисляется на сервере
        return cls(
            page=page,
            page_size=page_size,
            total=0  # total устанавливается отдельно через update_total()
        )
    
    @classmethod
    def from_payload(cls, payload: str) -> "Pagination":
        """
        Десериализация из строки callback_data
        Поддерживает два формата:
        1. Компактный формат: p{page}s{page_size} (например, p2s10)
        2. JSON формат: {"p": 2, "s": 10} (для обратной совместимости)
        3. Просто номер страницы: "2" (fallback)
        """
        # Пытаемся распарсить компактный формат: p2s10
        if payload.startswith("p") and "s" in payload:
            try:
                # p2s10 -> ["p2", "10"]
                parts = payload[1:].split("s", 1)
                page = int(parts[0])
                page_size = int(parts[1]) if len(parts) > 1 else 10
                return cls(page=page, page_size=page_size)
            except (ValueError, IndexError):
                pass
        
        # Пытаемся распарсить JSON формат (для обратной совместимости)
        try:
            data = json.loads(payload)
            return cls.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            pass
        
        # Fallback: пытаемся распарсить как просто номер страницы
        try:
            page = int(payload)
            return cls(page=page)
        except ValueError:
            return cls()  # Возвращаем дефолтные значения
    
    def update_total(self, total: int):
        """Обновляет общее количество элементов"""
        self.total = max(0, total)
        # Корректируем текущую страницу, если она выходит за пределы
        if self.page > self.total_pages and self.total_pages > 0:
            self.page = self.total_pages
