"""Smoke-тесты для UI экранов подписки (после унификации меню).

Меню одно для всех (MENU_PLAN_CODES), кнопка "🔄 Продлить" появляется
если у юзера есть last_plan_code. Cross-cohort guard'а больше нет —
проверяется только is_valid_plan_code.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.plans import (
    LEGACY_PLAN_CODES,
    MENU_PLAN_CODES,
    NEW_PLAN_CODES,
    get_plan_price,
)
from app.ui.keyboards.subscription import (
    build_subscription_plan_detail_keyboard,
    build_subscription_plans_keyboard,
)
from app.ui.renderers.subscription import (
    render_subscription_plans,
)
from app.ui.viewmodels.subscription import (
    SubscriptionPlanDetailViewModel,
    SubscriptionViewModel,
)


def _flatten_buttons(kb):
    return [btn for row in kb.inline_keyboard for btn in row]


class TestPlansKeyboardUnified:
    """Меню тарифов одно для всех — 3 новых тарифа."""

    @pytest.mark.asyncio
    async def test_no_last_plan_renders_three_new_plans(self):
        vm = SubscriptionViewModel(last_plan_code=None)
        kb = await build_subscription_plans_keyboard(vm)
        buttons = _flatten_buttons(kb)
        plan_buttons = [b for b in buttons if b.callback_data and ":select:" in b.callback_data]
        assert len(plan_buttons) == 3
        suffixes = [b.callback_data.rsplit(":", 1)[-1] for b in plan_buttons]
        assert set(suffixes) == set(MENU_PLAN_CODES)
        # Не должно быть legacy-кодов
        assert "basic" not in suffixes
        assert "premium" not in suffixes
        # Не должно быть кнопки extend
        extend_buttons = [b for b in buttons if b.callback_data and ":extend:" in b.callback_data]
        assert extend_buttons == []

    @pytest.mark.asyncio
    async def test_legacy_user_with_basic_sub_sees_extend_plus_three_new(self):
        """Юзер с активной basic-подпиской видит Продлить + те же 3 новых тарифа."""
        vm = SubscriptionViewModel(last_plan_code="basic")
        kb = await build_subscription_plans_keyboard(vm)
        buttons = _flatten_buttons(kb)

        extend_buttons = [b for b in buttons if b.callback_data and ":extend:" in b.callback_data]
        assert len(extend_buttons) == 1
        # Текст содержит имя "Базовый тариф"
        assert "Базовый" in extend_buttons[0].text
        assert "🔄" in extend_buttons[0].text

        plan_buttons = [b for b in buttons if b.callback_data and ":select:" in b.callback_data]
        suffixes = [b.callback_data.rsplit(":", 1)[-1] for b in plan_buttons]
        assert set(suffixes) == set(MENU_PLAN_CODES)

    @pytest.mark.asyncio
    async def test_new_user_with_pro_sub_sees_extend_plus_three_new(self):
        vm = SubscriptionViewModel(last_plan_code="pro")
        kb = await build_subscription_plans_keyboard(vm)
        buttons = _flatten_buttons(kb)

        extend_buttons = [b for b in buttons if b.callback_data and ":extend:" in b.callback_data]
        assert len(extend_buttons) == 1
        assert "Premium" in extend_buttons[0].text  # display name pro = "Premium"

    @pytest.mark.asyncio
    async def test_button_prices_use_new_catalog(self):
        vm = SubscriptionViewModel(last_plan_code=None)
        kb = await build_subscription_plans_keyboard(vm)
        buttons = _flatten_buttons(kb)
        lite_btn = next(b for b in buttons if b.callback_data and b.callback_data.endswith(":lite"))
        assert "129" in lite_btn.text
        std_btn = next(b for b in buttons if b.callback_data and b.callback_data.endswith(":standard"))
        assert "249" in std_btn.text
        pro_btn = next(b for b in buttons if b.callback_data and b.callback_data.endswith(":pro"))
        assert "449" in pro_btn.text


class TestPlanDetailKeyboardPrices:
    """4 кнопки периодов, цены из PLAN_CATALOG. Detail работает для всех 5 покупаемых кодов."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("plan_code", ["basic", "premium", "lite", "standard", "pro"])
    async def test_period_buttons_match_catalog(self, plan_code):
        vm = SubscriptionPlanDetailViewModel(
            plan_code=plan_code, plan_name="X", period_months=0, amount=0, features=[],
        )
        kb = await build_subscription_plan_detail_keyboard(vm)
        buttons = _flatten_buttons(kb)
        for months in (1, 3, 6, 12):
            expected_price = get_plan_price(plan_code, months)
            assert expected_price > 0
            btn = next(
                b for b in buttons
                if b.callback_data and f":select_period:{plan_code}_{months}" in b.callback_data
            )
            assert str(expected_price) in btn.text

    @pytest.mark.asyncio
    async def test_period_buttons_skip_unknown_plan(self):
        vm = SubscriptionPlanDetailViewModel(
            plan_code="garbage", plan_name="?", period_months=0, amount=0, features=[],
        )
        kb = await build_subscription_plan_detail_keyboard(vm)
        buttons = _flatten_buttons(kb)
        period_btns = [b for b in buttons if b.callback_data and ":select_period:" in b.callback_data]
        assert period_btns == []


class TestPlansRenderer:
    @pytest.mark.asyncio
    async def test_no_last_plan_renders_three_new(self):
        vm = SubscriptionViewModel(last_plan_code=None)
        text = await render_subscription_plans(vm)
        assert "Lite" in text
        assert "Standard" in text
        assert "Premium" in text
        # Hint про продление не показывается
        assert "Продлить" not in text

    @pytest.mark.asyncio
    async def test_with_last_plan_shows_extend_hint(self):
        vm = SubscriptionViewModel(last_plan_code="basic")
        text = await render_subscription_plans(vm)
        # Hint про продление + название старого тарифа
        assert "Продлить" in text or "продлить" in text
        assert "Базовый тариф" in text
        # Меню всё равно содержит 3 новых
        assert "Lite" in text
        assert "Standard" in text


class TestExtendAction:
    """action="extend" должен дёргать get_user_last_plan и навигировать на detail."""

    @pytest.mark.asyncio
    async def test_extend_with_no_last_plan_returns_false(self):
        from app.ui.screens.subscription import SubscriptionPlansScreen
        # Передаём dict — он не CallbackQuery, isinstance-ветка с answer не сработает.
        screen = SubscriptionPlansScreen()
        with patch("app.services.users.get_user_last_plan", return_value=None):
            ok = await screen.handle_action(
                action="extend", payload="", message_or_callback={}, user_id=42,
            )
        assert ok is False

    @pytest.mark.asyncio
    async def test_extend_with_invalid_plan_returns_false(self):
        from app.ui.screens.subscription import SubscriptionPlansScreen
        screen = SubscriptionPlansScreen()
        with patch("app.services.users.get_user_last_plan", return_value="garbage_plan"):
            ok = await screen.handle_action(
                action="extend", payload="", message_or_callback={}, user_id=42,
            )
        assert ok is False


class TestStaleCallbackForLegacyCode:
    """select на legacy-код больше не блокируется — допустимо для продления."""

    @pytest.mark.asyncio
    async def test_select_basic_no_longer_blocked(self):
        """Раньше cross-cohort guard блокировал; теперь допустимо
        (юзер мог нажать на старую stale-кнопку, и это его осознанный выбор)."""
        from app.ui.screens.subscription import SubscriptionPlansScreen
        from unittest.mock import AsyncMock, MagicMock

        screen = SubscriptionPlansScreen()
        # Просто проверяем что валидация не упадёт на is_valid_plan_code("basic")
        # Полный путь требует mock screen_manager; ограничиваемся unit-уровнем.
        from app.core.plans import is_valid_plan_code
        assert is_valid_plan_code("basic") is True
        assert is_valid_plan_code("premium") is True
        assert is_valid_plan_code("garbage") is False
