"""
ViewModel для экранов подключения
"""
from typing import Optional
from app.ui.viewmodels.base import BaseViewModel
from app.ui.screens import ScreenID


class ConnectViewModel(BaseViewModel):
    """ViewModel для экрана подключения VPN"""
    
    def __init__(
        self,
        has_subscription: bool,
        subscription_url: Optional[str] = None,
        status: str = "loading",  # "loading", "success", "error", "no_subscription"
        error_message: Optional[str] = None,
        # --- Обход (две подписки) ---
        is_pro: bool = False,
        obhod_url: Optional[str] = None,
        obhod_used_bytes: Optional[int] = None,
        obhod_limit_bytes: Optional[int] = None,
        obhod_active: bool = False,
    ):
        self.has_subscription = has_subscription
        self.subscription_url = subscription_url
        self.status = status  # loading, success, error, no_subscription
        self.error_message = error_message
        # is_pro=True → показываем блок обхода со ссылкой/остатком и кнопкой «больше».
        # is_pro=False → показываем заглушку «Обход доступен в Pro».
        self.is_pro = is_pro
        self.obhod_url = obhod_url
        self.obhod_used_bytes = obhod_used_bytes
        self.obhod_limit_bytes = obhod_limit_bytes
        self.obhod_active = obhod_active
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.CONNECT
    
    @property
    def is_loading(self) -> bool:
        return self.status == "loading"
    
    @property
    def is_success(self) -> bool:
        return self.status == "success" and self.subscription_url is not None
    
    @property
    def is_error(self) -> bool:
        return self.status == "error"
    
    @property
    def has_no_subscription(self) -> bool:
        return self.status == "no_subscription" or not self.has_subscription