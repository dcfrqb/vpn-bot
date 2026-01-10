"""
ViewModels для экранов ошибок
"""
from typing import Optional
from app.ui.viewmodels.base import BaseViewModel
from app.ui.screens import ScreenID


class ErrorViewModel(BaseViewModel):
    """ViewModel для экрана ошибки"""
    
    def __init__(
        self,
        error_message: str = "Произошла ошибка",
        request_id: Optional[str] = None,
        error_type: str = "general"
    ):
        self.error_message = error_message
        self.request_id = request_id
        self.error_type = error_type
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ERROR


class AccessDeniedViewModel(BaseViewModel):
    """ViewModel для экрана отказа в доступе"""
    
    def __init__(self, message: str = "У вас нет прав для доступа к этому экрану", reason: str = None):
        self.message = message
        self.reason = reason or message
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.ACCESS_DENIED


class RemnaUnavailableViewModel(BaseViewModel):
    """ViewModel для экрана недоступности Remna"""
    
    def __init__(self, message: str = "Сервис временно недоступен"):
        self.message = message
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.REMNA_UNAVAILABLE