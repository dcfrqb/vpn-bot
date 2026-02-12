"""Тесты для select_period в ACTION_MAP"""
import pytest
from app.ui.action_map import get_action_effect, is_action_allowed
from app.ui.screens import ScreenID


class TestSelectPeriodActionMap:
    """Тесты: select_period разрешён для SUBSCRIPTION_PLAN_DETAIL"""

    def test_select_period_in_action_map(self):
        """select_period должен быть в ACTION_MAP для SUBSCRIPTION_PLAN_DETAIL"""
        effect = get_action_effect(ScreenID.SUBSCRIPTION_PLAN_DETAIL, "select_period")
        assert effect is not None
        effect_type, target_screen = effect
        assert effect_type == "STATE"
        assert target_screen is None

    def test_select_period_is_allowed(self):
        """select_period должен быть разрешён для SUBSCRIPTION_PLAN_DETAIL"""
        assert is_action_allowed(ScreenID.SUBSCRIPTION_PLAN_DETAIL, "select_period") is True

    def test_select_period_not_on_other_screens(self):
        """select_period НЕ разрешён для экранов, где кнопки нет"""
        assert is_action_allowed(ScreenID.SUBSCRIPTION_PLANS, "select_period") is False
        assert is_action_allowed(ScreenID.MAIN_MENU, "select_period") is False
        assert is_action_allowed(ScreenID.PROFILE, "select_period") is False
