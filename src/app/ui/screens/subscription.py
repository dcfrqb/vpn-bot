"""
Экраны подписки
"""
from typing import Optional, Union
from aiogram import types
from app.ui.screens.base import BaseScreen
from app.ui.screens import ScreenID
from app.ui.viewmodels.subscription import (
    SubscriptionViewModel,
    SubscriptionPlanDetailViewModel,
    SubscriptionPaymentViewModel
)
from app.ui.renderers.subscription import (
    render_subscription_plans,
    render_subscription_plan_detail,
    render_subscription_payment
)
from app.ui.keyboards.subscription import (
    build_subscription_plans_keyboard,
    build_subscription_plan_detail_keyboard,
    build_subscription_payment_keyboard
)
from app.logger import logger


class SubscriptionPlansScreen(BaseScreen):
    """Экран выбора тарифов"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.SUBSCRIPTION_PLANS
    
    async def render(self, viewmodel: SubscriptionViewModel) -> str:
        return await render_subscription_plans(viewmodel)
    
    async def build_keyboard(self, viewmodel: SubscriptionViewModel) -> types.InlineKeyboardMarkup:
        return await build_subscription_plans_keyboard(viewmodel)
    
    async def create_viewmodel(self, **kwargs) -> SubscriptionViewModel:
        return SubscriptionViewModel()
    
    async def handle_action(
        self,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int]
    ) -> bool:
        """
        Обрабатывает действия экрана (select - выбор тарифа)
        
        Args:
            action: Действие (select)
            payload: Данные (plan_code: "basic" или "premium")
            message_or_callback: Message или CallbackQuery
            user_id: ID пользователя
            
        Returns:
            True, если действие обработано
        """
        if action == "select":
            # Выбор тарифа - создаем ViewModel для детального экрана тарифа с предустановленным периодом 1 месяц
            plan_code = payload
            
            # Определяем данные тарифа и цену для 1 месяца
            if plan_code == "basic":
                plan_name = "Базовый тариф"
                period_months = 1
                amount = 99
                features = [
                    "Неограниченный трафик и скорость",
                    "Поддержка разных устройств",
                    "YouTube без рекламы",
                    "Сервера NL"
                ]
            elif plan_code == "premium":
                plan_name = "Премиум тариф"
                period_months = 1
                amount = 199
                features = [
                    "Неограниченный трафик и скорость",
                    "Поддержка разных устройств",
                    "YouTube без рекламы",
                    "Сервера NL, USA, FIN"
                ]
            else:
                logger.warning(f"Неизвестный plan_code: {plan_code}")
                return False
            
            logger.info(f"Пользователь {user_id} выбрал тариф: {plan_code} ({plan_name}), период: {period_months} месяц, сумма: {amount}₽")
            
            # Создаем ViewModel для детального экрана тарифа с предустановленным периодом 1 месяц
            detail_screen = SubscriptionPlanDetailScreen()
            viewmodel = await detail_screen.create_viewmodel(
                plan_code=plan_code,
                plan_name=plan_name,
                period_months=period_months,  # Предустановленный период 1 месяц
                amount=amount,
                features=features
            )
            
            # Показываем детальный экран через ScreenManager
            from app.ui.screen_manager import get_screen_manager
            screen_manager = get_screen_manager()
            return await screen_manager.navigate(
                from_screen_id=ScreenID.SUBSCRIPTION_PLANS,
                to_screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
                message_or_callback=message_or_callback,
                viewmodel=viewmodel,
                edit=True
            )
        
        return False


class SubscriptionPlanDetailScreen(BaseScreen):
    """Экран детальной информации о тарифе"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.SUBSCRIPTION_PLAN_DETAIL
    
    async def render(self, viewmodel: SubscriptionPlanDetailViewModel) -> str:
        return await render_subscription_plan_detail(viewmodel)
    
    async def build_keyboard(self, viewmodel: SubscriptionPlanDetailViewModel) -> types.InlineKeyboardMarkup:
        return await build_subscription_plan_detail_keyboard(viewmodel)
    
    async def create_viewmodel(
        self,
        plan_code: str = "basic",
        plan_name: str = "Базовый тариф",
        period_months: int = 1,
        amount: int = 99,
        features: list[str] = None
    ) -> SubscriptionPlanDetailViewModel:
        if features is None:
            if plan_code == "basic":
                features = [
                    "Неограниченный трафик и скорость",
                    "Поддержка разных устройств",
                    "YouTube без рекламы",
                    "Сервера NL"
                ]
            else:  # premium
                features = [
                    "Неограниченный трафик и скорость",
                    "Поддержка разных устройств",
                    "YouTube без рекламы",
                    "Сервера NL, USA, FIN"
                ]
        
        return SubscriptionPlanDetailViewModel(
            plan_code=plan_code,
            plan_name=plan_name,
            period_months=period_months,
            amount=amount,
            features=features
        )
    
    async def handle_action(
        self,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int]
    ) -> bool:
        """
        Обрабатывает действия экрана (select - выбор тарифа, select_period - выбор периода)
        
        Args:
            action: Действие (select, select_period)
            payload: Данные (plan_code для select, plan_code_period для select_period)
            message_or_callback: Message или CallbackQuery
            user_id: ID пользователя
            
        Returns:
            True, если действие обработано
        """
        from app.ui.screen_manager import get_screen_manager
        
        if action == "select":
            # Выбор тарифа - создаем ViewModel с данными тарифа и показываем через ScreenManager
            plan_code = payload
            
            # Определяем данные тарифа
            if plan_code == "basic":
                plan_name = "Базовый тариф"
                features = [
                    "Неограниченный трафик и скорость",
                    "Поддержка разных устройств",
                    "YouTube без рекламы",
                    "Сервера NL"
                ]
            else:  # premium
                plan_name = "Премиум тариф"
                features = [
                    "Неограниченный трафик и скорость",
                    "Поддержка разных устройств",
                    "YouTube без рекламы",
                    "Сервера NL, USA, FIN"
                ]
            
            # Создаем ViewModel с данными тарифа (по умолчанию период 0 - не выбран)
            # Пользователь выберет период на следующем шаге
            viewmodel = await self.create_viewmodel(
                plan_code=plan_code,
                plan_name=plan_name,
                period_months=0,  # 0 означает, что период еще не выбран
                amount=0,  # Сумма будет установлена при выборе периода
                features=features
            )
            
            # Показываем экран через ScreenManager
            screen_manager = get_screen_manager()
            return await screen_manager.navigate(
                from_screen_id=ScreenID.SUBSCRIPTION_PLANS,
                to_screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
                message_or_callback=message_or_callback,
                viewmodel=viewmodel,
                edit=True
            )
        
        elif action == "select_period":
            # Выбор периода подписки - обновляем ViewModel с выбранным периодом
            # payload формат: "plan_code_period" (например "basic_1" или "premium_3")
            parts = payload.split("_")
            if len(parts) != 2:
                logger.warning(f"Неверный формат payload для select_period: {payload}")
                return False
            
            plan_code = parts[0]
            period_months = int(parts[1])
            
            # Определяем цену в зависимости от тарифа и периода
            if plan_code == "basic":
                plan_name = "Базовый тариф"
                periods = {"1": 99, "3": 249, "6": 499, "12": 899}
                features = [
                    "Неограниченный трафик и скорость",
                    "Поддержка разных устройств",
                    "YouTube без рекламы",
                    "Сервера NL"
                ]
            else:  # premium
                plan_name = "Премиум тариф"
                periods = {"1": 199, "3": 549, "6": 999, "12": 1799}
                features = [
                    "Неограниченный трафик и скорость",
                    "Поддержка разных устройств",
                    "YouTube без рекламы",
                    "Сервера NL, USA, FIN"
                ]
            
            amount = periods.get(str(period_months), 0)
            if amount == 0:
                logger.warning(f"Неверный период для тарифа {plan_code}: {period_months}")
                return False
            
            logger.info(f"Пользователь {user_id} выбрал тариф {plan_code} на {period_months} месяц(а/ев), сумма: {amount}₽")
            
            # Создаем обновленный ViewModel с выбранным периодом
            viewmodel = await self.create_viewmodel(
                plan_code=plan_code,
                plan_name=plan_name,
                period_months=period_months,
                amount=amount,
                features=features
            )
            
            # Обновляем экран через ScreenManager (STATE - обновление без изменения backstack)
            screen_manager = get_screen_manager()
            return await screen_manager.show_screen(
                screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
                message_or_callback=message_or_callback,
                viewmodel=viewmodel,
                edit=True,
                user_id=user_id
            )
        
        return False


class SubscriptionPaymentScreen(BaseScreen):
    """Экран оплаты подписки"""
    
    @property
    def screen_id(self) -> ScreenID:
        return ScreenID.SUBSCRIPTION_PAYMENT
    
    async def render(self, viewmodel: SubscriptionPaymentViewModel) -> str:
        return await render_subscription_payment(viewmodel)
    
    async def build_keyboard(self, viewmodel: SubscriptionPaymentViewModel) -> types.InlineKeyboardMarkup:
        return await build_subscription_payment_keyboard(viewmodel)
    
    async def create_viewmodel(
        self,
        plan_code: str = "",
        plan_name: str = "",
        period_months: int = 0,
        amount: int = 0,
        payment_url: str = None,
        crypto_address: str = None,
        external_id: str = None,
    ) -> SubscriptionPaymentViewModel:
        return SubscriptionPaymentViewModel(
            plan_code=plan_code,
            plan_name=plan_name,
            period_months=period_months,
            amount=amount,
            payment_url=payment_url,
            crypto_address=crypto_address,
            external_id=external_id,
        )