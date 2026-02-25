"""
ViewModel для экранов подписки
"""
from typing import Optional
from app.ui.viewmodels.base import BaseViewModel
from app.ui.screens import ScreenID


class SubscriptionViewModel(BaseViewModel):
    """ViewModel для экрана выбора тарифов"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.SUBSCRIPTION_PLANS


class SubscriptionPlanDetailViewModel(BaseViewModel):
    """ViewModel для детального экрана тарифа"""
    
    def __init__(
        self,
        plan_code: str,  # "basic" или "premium"
        plan_name: str,
        period_months: int,
        amount: int,
        features: list[str]
    ):
        self.plan_code = plan_code
        self.plan_name = plan_name
        self.period_months = period_months
        self.amount = amount
        self.features = features
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.SUBSCRIPTION_PLAN_DETAIL


class SubscriptionPaymentViewModel(BaseViewModel):
    """ViewModel для экрана оплаты"""
    
    def __init__(
        self,
        plan_code: str,
        plan_name: str,
        period_months: int,
        amount: int,
        payment_url: Optional[str] = None,
        crypto_address: Optional[str] = None,
        external_id: Optional[str] = None,
    ):
        self.plan_code = plan_code
        self.plan_name = plan_name
        self.period_months = period_months
        self.amount = amount
        self.payment_url = payment_url
        self.crypto_address = crypto_address
        self.external_id = external_id
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.SUBSCRIPTION_PAYMENT