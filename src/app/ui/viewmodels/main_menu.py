"""
ViewModel для главного меню
"""
from typing import Optional, TYPE_CHECKING
from app.ui.viewmodels.base import BaseViewModel
from app.ui.screens import ScreenID

if TYPE_CHECKING:
    from app.routers.subscription_view import SubscriptionViewModel


class MainMenuViewModel(BaseViewModel):
    """ViewModel для главного меню"""
    
    def __init__(
        self,
        user_id: int,
        user_first_name: Optional[str] = None,
        user_last_name: Optional[str] = None,
        user_username: Optional[str] = None,
        subscription_view_model: Optional['SubscriptionViewModel'] = None,
        is_admin: bool = False
    ):
        self.user_id = user_id
        self.user_first_name = user_first_name
        self.user_last_name = user_last_name
        self.user_username = user_username
        self.subscription_view_model = subscription_view_model
        self.is_admin = is_admin
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.MAIN_MENU
    
    @property
    def has_subscription(self) -> bool:
        """Проверяет, есть ли активная подписка"""
        return self.subscription_view_model is not None and self.subscription_view_model.is_active