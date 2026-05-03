"""
Экраны подписки.

Меню тарифов одно для всех (MENU_PLAN_CODES = lite/standard/pro). Если у юзера
есть последняя покупаемая подписка (`get_user_last_plan`), на экране плюсом
рендерится кнопка "🔄 Продлить", ведущая на детальный экран этого plan_code
с актуальными для него ценами (legacy basic/premium → старые, new → новые).

Cross-cohort guard'а больше нет — `is_valid_plan_code` достаточно.
"""
from typing import Optional, Union

from aiogram import types

from app.core.plans import (
    get_plan_features,
    get_plan_name,
    get_plan_price,
    is_valid_plan_code,
)
from app.logger import logger
from app.ui.keyboards.subscription import (
    build_subscription_payment_keyboard,
    build_subscription_plan_detail_keyboard,
    build_subscription_plans_keyboard,
)
from app.ui.renderers.subscription import (
    render_subscription_payment,
    render_subscription_plan_detail,
    render_subscription_plans,
)
from app.ui.screens import ScreenID
from app.ui.screens.base import BaseScreen
from app.ui.viewmodels.subscription import (
    SubscriptionPaymentViewModel,
    SubscriptionPlanDetailViewModel,
    SubscriptionViewModel,
)


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
        """Подтягивает last_plan_code для рендера кнопки 'Продлить'.

        user_id может не быть передан (legacy callers) — тогда last_plan_code=None
        и кнопка просто не показывается.
        """
        user_id = kwargs.get("user_id")
        last_plan_code: Optional[str] = None
        if user_id is not None:
            try:
                from app.services.users import get_user_last_plan
                last_plan_code = await get_user_last_plan(int(user_id))
            except Exception as e:
                logger.debug(
                    f"create_viewmodel: get_user_last_plan failed user_id={user_id} err={e}"
                )
        return SubscriptionViewModel(last_plan_code=last_plan_code)

    async def handle_action(
        self,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int]
    ) -> bool:
        """select - выбор нового тарифа из меню; extend - продление текущего."""
        from app.ui.screen_manager import get_screen_manager

        if action == "extend":
            # Дёргаем live last_plan, чтобы на race-условия (юзер мог купить
            # что-то в другом окне) была свежая инфа.
            last_plan_code: Optional[str] = None
            if user_id is not None:
                try:
                    from app.services.users import get_user_last_plan
                    last_plan_code = await get_user_last_plan(int(user_id))
                except Exception as e:
                    logger.warning(f"extend: get_user_last_plan failed user_id={user_id} err={e}")

            if not last_plan_code or not is_valid_plan_code(last_plan_code):
                # Race / fail-safe: VM показала кнопку, а сейчас плана нет.
                if isinstance(message_or_callback, types.CallbackQuery):
                    await message_or_callback.answer(
                        "У вас нет подписки для продления", show_alert=True
                    )
                return False

            viewmodel = await SubscriptionPlanDetailScreen().create_viewmodel(
                plan_code=last_plan_code,
                period_months=0,  # юзер выберет период
                amount=0,
            )
            screen_manager = get_screen_manager()
            return await screen_manager.navigate(
                from_screen_id=ScreenID.SUBSCRIPTION_PLANS,
                to_screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
                message_or_callback=message_or_callback,
                viewmodel=viewmodel,
                edit=True,
            )

        if action != "select":
            return False

        plan_code = payload
        if not is_valid_plan_code(plan_code):
            logger.warning(f"select: невалидный plan_code {plan_code!r}")
            return False

        plan_name = get_plan_name(plan_code)
        period_months = 1
        amount = get_plan_price(plan_code, period_months)
        features = get_plan_features(plan_code)

        if amount <= 0:
            logger.warning(f"select: нет цены для {plan_code}/{period_months}m")
            return False

        logger.info(
            f"Пользователь {user_id} выбрал тариф: {plan_code} ({plan_name}), "
            f"период: {period_months} месяц, сумма: {amount}₽"
        )

        viewmodel = await SubscriptionPlanDetailScreen().create_viewmodel(
            plan_code=plan_code,
            plan_name=plan_name,
            period_months=period_months,
            amount=amount,
            features=features,
        )

        screen_manager = get_screen_manager()
        return await screen_manager.navigate(
            from_screen_id=ScreenID.SUBSCRIPTION_PLANS,
            to_screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
            message_or_callback=message_or_callback,
            viewmodel=viewmodel,
            edit=True
        )


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
        plan_name: Optional[str] = None,
        period_months: int = 1,
        amount: Optional[int] = None,
        features: Optional[list[str]] = None,
    ) -> SubscriptionPlanDetailViewModel:
        # Дефолты тянем из каталога — никаких хардкодов под basic/premium.
        if plan_name is None:
            plan_name = get_plan_name(plan_code)
        if features is None:
            features = get_plan_features(plan_code)
        if amount is None:
            amount = get_plan_price(plan_code, period_months)

        return SubscriptionPlanDetailViewModel(
            plan_code=plan_code,
            plan_name=plan_name,
            period_months=period_months,
            amount=amount,
            features=features,
        )

    async def handle_action(
        self,
        action: str,
        payload: str,
        message_or_callback: Union[types.Message, types.CallbackQuery, dict],
        user_id: Optional[int]
    ) -> bool:
        """select - смена тарифа на детальном экране, select_period - выбор периода."""
        from app.ui.screen_manager import get_screen_manager

        if action == "select":
            plan_code = payload
            if not is_valid_plan_code(plan_code):
                logger.warning(f"detail/select: невалидный plan_code {plan_code!r}")
                return False

            viewmodel = await self.create_viewmodel(
                plan_code=plan_code,
                period_months=0,
                amount=0,
            )

            screen_manager = get_screen_manager()
            return await screen_manager.navigate(
                from_screen_id=ScreenID.SUBSCRIPTION_PLANS,
                to_screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
                message_or_callback=message_or_callback,
                viewmodel=viewmodel,
                edit=True,
            )

        if action == "select_period":
            try:
                plan_code, period_raw = payload.rsplit("_", 1)
                period_months = int(period_raw)
            except (ValueError, AttributeError):
                logger.warning(f"Неверный формат payload select_period: {payload!r}")
                return False

            if not is_valid_plan_code(plan_code):
                logger.warning(f"detail/select_period: невалидный plan_code {plan_code!r}")
                return False

            amount = get_plan_price(plan_code, period_months)
            if amount <= 0:
                logger.warning(f"Неверный период/тариф: plan={plan_code} months={period_months}")
                return False

            plan_name = get_plan_name(plan_code)
            features = get_plan_features(plan_code)

            logger.info(
                f"Пользователь {user_id} выбрал тариф {plan_code} на {period_months} "
                f"месяц(а/ев), сумма: {amount}₽"
            )

            viewmodel = await self.create_viewmodel(
                plan_code=plan_code,
                plan_name=plan_name,
                period_months=period_months,
                amount=amount,
                features=features,
            )

            screen_manager = get_screen_manager()
            return await screen_manager.show_screen(
                screen_id=ScreenID.SUBSCRIPTION_PLAN_DETAIL,
                message_or_callback=message_or_callback,
                viewmodel=viewmodel,
                edit=True,
                user_id=user_id,
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
        payment_url: Optional[str] = None,
        external_id: Optional[str] = None,
    ) -> SubscriptionPaymentViewModel:
        return SubscriptionPaymentViewModel(
            plan_code=plan_code,
            plan_name=plan_name,
            period_months=period_months,
            amount=amount,
            payment_url=payment_url,
            external_id=external_id,
        )
