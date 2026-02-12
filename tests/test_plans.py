"""Тесты для справочника тарифов (core.plans)"""
import pytest
from app.core.plans import get_plan_name, is_valid_plan_code, PLAN_NAMES


class TestGetPlanName:
    """Тесты get_plan_name"""

    def test_basic_plan(self):
        assert get_plan_name("basic") == "Базовый тариф"
        assert get_plan_name("BASIC") == "Базовый тариф"
        assert get_plan_name(" Basic ") == "Базовый тариф"

    def test_premium_plan(self):
        assert get_plan_name("premium") == "Премиум тариф"

    def test_trial_plan(self):
        assert get_plan_name("trial") == "Пробный период"

    def test_unknown_plan_returns_fallback(self):
        result = get_plan_name("unknown")
        assert result == "Тариф (обновите меню)"

    def test_none_returns_fallback(self):
        assert get_plan_name(None) == "Тариф (обновите меню)"

    def test_empty_string_returns_fallback(self):
        assert get_plan_name("") == "Тариф (обновите меню)"


class TestIsValidPlanCode:
    """Тесты is_valid_plan_code"""

    def test_valid_codes(self):
        assert is_valid_plan_code("basic") is True
        assert is_valid_plan_code("premium") is True
        assert is_valid_plan_code("trial") is True

    def test_invalid_codes(self):
        assert is_valid_plan_code("unknown") is False
        assert is_valid_plan_code(None) is False
        assert is_valid_plan_code("") is False
