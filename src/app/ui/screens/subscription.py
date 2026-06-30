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
        """select - выбор нового тарифа; extend - продление; obhod - пакеты обхода."""
        from app.ui.screen_manager import get_screen_manager

        if action == "obhod":
            # Категория «Обход +трафик» внутри экрана подписки (без новой кнопки в меню).
            from app.ui.keyboards.subscription import build_obhod_packages_keyboard
            from app.ui.renderers.subscription import render_obhod_packages

            text = render_obhod_packages()
            keyboard = build_obhod_packages_keyboard()
            if isinstance(message_or_callback, types.CallbackQuery):
                try:
                    await message_or_callback.message.edit_text(
                        text, reply_markup=keyboard, parse_mode="HTML"
                    )
                except Exception as e:
                    logger.debug(f"obhod packages render edit failed: {e}")
                    await message_or_callback.answer()
                return True
            return False

        if action == "buy_obhod":
            # payload = код пакета. Доступно только при реальной цене (placeholder=0 → нет).
            from app.core.plans import (
                get_obhod_package,
                is_obhod_package_purchasable,
            )
            from app.ui.keyboards.subscription import build_obhod_packages_keyboard

            # callback уже отвечен в ui_callback_handler ДО хендлера, поэтому
            # callback.answer(текст) здесь Telegram уже не покажет. Для обратной
            # связи (отказ/ошибка) редактируем сообщение, а не шлём второй answer.
            async def _obhod_notice(text: str) -> bool:
                if isinstance(message_or_callback, types.CallbackQuery):
                    try:
                        await message_or_callback.message.edit_text(
                            text,
                            reply_markup=build_obhod_packages_keyboard(),
                            parse_mode="HTML",
                        )
                    except Exception as _e:
                        logger.debug(f"buy_obhod notice edit failed: {_e}")
                return True

            package_code = payload
            if not is_obhod_package_purchasable(package_code):
                return await _obhod_notice("Этот пакет пока недоступен.")

            # H1: пакет поднимает кап на обходном юзере и применим только при
            # активном обходе (то есть активном Pro). Проверяем ДО создания платежа,
            # иначе оплата пройдёт, а кап не выдастся (apply_obhod_package вернёт
            # False) и деньги уйдут «в никуда». Pro мог истечь между показом кнопки
            # и оплатой — поэтому проверка свежая, по БД.
            has_active = False
            if user_id is not None:
                try:
                    from app.services.obhod_service import has_active_obhod
                    has_active = await has_active_obhod(int(user_id))
                except Exception as e:
                    logger.warning(
                        f"buy_obhod: проверка активного обхода упала user_id={user_id} err={e}"
                    )
            if not has_active:
                return await _obhod_notice(
                    "🛡 <b>Пакет обхода</b>\n\n"
                    "Пакеты доступны только при активном тарифе Pro. "
                    "Оформите или продлите Pro, потом возьмите пакет."
                )

            meta = get_obhod_package(package_code)
            amount = int(meta["price"])
            period_months = int(meta.get("period_months", 1))
            plan_name = meta["display"]

            from app.services.payments.yookassa import create_payment
            from app.keyboards import get_payment_keyboard

            try:
                payment_url, external_id = await create_payment(
                    amount_rub=amount,
                    description=f"CRS VPN - {plan_name}",
                    user_id=int(user_id) if user_id else 0,
                    plan_code=package_code,
                    period_months=period_months,
                )
            except Exception as e:
                logger.error(f"buy_obhod: create_payment failed package={package_code} err={e}")
                return await _obhod_notice(
                    "❌ Не удалось создать платёж. Попробуйте позже."
                )

            if isinstance(message_or_callback, types.CallbackQuery):
                await message_or_callback.message.edit_text(
                    f"💳 <b>{plan_name}</b>\n"
                    f"💰 <b>Сумма:</b> {amount}₽\n\n"
                    "🔗 <b>Для оплаты перейдите по ссылке:</b>\n"
                    f"<a href='{payment_url}'>Оплатить пакет обхода</a>\n\n"
                    "💡 После оплаты лимит обхода поднимется автоматически.",
                    reply_markup=get_payment_keyboard(payment_url, external_id),
                    parse_mode="HTML",
                )
            return True

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
