"""
Базовый класс ViewModel
"""
from abc import ABC
from app.ui.screens import ScreenID


class BaseViewModel(ABC):
    """Базовый класс для ViewModel"""
    
    @property
    def screen_id(self) -> ScreenID:
        """ID экрана, для которого предназначена эта ViewModel"""
        raise NotImplementedError