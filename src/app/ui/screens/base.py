"""
Базовый класс для экранов
"""
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from aiogram import types
from app.ui.screens import ScreenID

if TYPE_CHECKING:
    from app.ui.viewmodels.base import BaseViewModel


class BaseScreen(ABC):
    """Базовый класс экрана - определяет контракт для всех экранов"""
    
    @property
    @abstractmethod
    def screen_id(self) -> ScreenID:
        """ID экрана"""
        pass
    
    @abstractmethod
    async def render(self, viewmodel: 'BaseViewModel') -> str:
        """
        Рендерит текст экрана из ViewModel
        
        Args:
            viewmodel: ViewModel с данными для экрана
            
        Returns:
            HTML-форматированный текст
        """
        pass
    
    @abstractmethod
    async def build_keyboard(self, viewmodel: 'BaseViewModel') -> types.InlineKeyboardMarkup:
        """
        Строит клавиатуру для экрана из ViewModel
        
        Args:
            viewmodel: ViewModel с данными для экрана
            
        Returns:
            InlineKeyboardMarkup
        """
        pass
    
    @abstractmethod
    async def create_viewmodel(self, **kwargs) -> 'BaseViewModel':
        """
        Создает ViewModel для экрана из переданных параметров
        
        Args:
            **kwargs: Параметры для создания ViewModel
            
        Returns:
            BaseViewModel
        """
        pass