"""
ViewModel для экранов подписки.
"""
from typing import Optional
from app.ui.viewmodels.base import BaseViewModel
from app.ui.screens import ScreenID


class SubscriptionViewModel(BaseViewModel):
    """ViewModel для экрана выбора тарифов.

    last_plan_code: plan_code последней покупаемой подписки юзера, если есть.
    Используется только для рендера дополнительной кнопки "🔄 Продлить".
    Само меню тарифов одинаковое для всех (см. MENU_PLAN_CODES).
    """

    def __init__(self, last_plan_code: Optional[str] = None):
        self.last_plan_code = last_plan_code

    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.SUBSCRIPTION_PLANS


class SubscriptionPlanDetailViewModel(BaseViewModel):
    """ViewModel для детального экрана тарифа.

    Цены и фичи рендерятся из app.core.plans.PLAN_CATALOG по plan_code,
    так что один и тот же экран корректно работает для всех 6 кодов
    (basic/premium/lite/standard/pro/trial).
    """

    def __init__(
        self,
        plan_code: str,
        plan_name: str,
        period_months: int,
        amount: int,
        features: list[str],
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
        external_id: Optional[str] = None,
    ):
        self.plan_code = plan_code
        self.plan_name = plan_name
        self.period_months = period_months
        self.amount = amount
        self.payment_url = payment_url
        self.external_id = external_id

    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.SUBSCRIPTION_PAYMENT
