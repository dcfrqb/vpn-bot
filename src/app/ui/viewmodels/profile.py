"""
ViewModel для профиля пользователя
"""
from typing import Optional
from datetime import datetime
from app.ui.viewmodels.base import BaseViewModel
from app.ui.screens import ScreenID


class ProfileViewModel(BaseViewModel):
    """ViewModel для экрана профиля"""
    
    def __init__(
        self,
        user_id: int,
        username: Optional[str] = None,
        created_at: Optional[datetime] = None,
        subscription_plan: Optional[str] = None,
        subscription_valid_until: Optional[datetime] = None,
        subscription_days_left: Optional[int] = None,
        total_payments: int = 0,
        total_spent: float = 0.0
    ):
        self.user_id = user_id
        self.username = username
        self.created_at = created_at
        self.subscription_plan = subscription_plan
        self.subscription_valid_until = subscription_valid_until
        self.subscription_days_left = subscription_days_left
        self.total_payments = total_payments
        self.total_spent = total_spent
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.PROFILE
    
    @property
    def has_subscription(self) -> bool:
        """Проверяет, есть ли активная подписка"""
        return (
            self.subscription_plan is not None
            and self.subscription_valid_until is not None
            and self.subscription_valid_until > datetime.utcnow()
        )