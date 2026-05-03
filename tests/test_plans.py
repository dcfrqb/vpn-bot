"""Тесты для справочника тарифов (core.plans)"""
import pytest
from app.core.plans import (
    LEGACY_PLAN_CODES,
    MENU_PLAN_CODES,
    NEW_PLAN_CODES,
    PLAN_CATALOG,
    PLAN_NAMES,
    VALID_PLAN_CODES,
    get_plan_device_limit,
    get_plan_features,
    get_plan_name,
    get_plan_price,
    get_plan_squad,
    is_valid_plan_code,
)


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

    def test_new_plans(self):
        assert get_plan_name("lite") == "Lite"
        assert get_plan_name("standard") == "Standard"
        assert get_plan_name("pro") == "Pro"

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
        for code in ("basic", "premium", "trial", "lite", "standard", "pro"):
            assert is_valid_plan_code(code) is True

    def test_invalid_codes(self):
        assert is_valid_plan_code("unknown") is False
        assert is_valid_plan_code(None) is False
        assert is_valid_plan_code("") is False


class TestPlanCatalog:
    """Тесты структуры PLAN_CATALOG."""

    def test_all_legacy_and_new_present(self):
        for code in ("basic", "premium", "lite", "standard", "pro", "trial"):
            assert code in PLAN_CATALOG, f"missing {code} in PLAN_CATALOG"

    def test_each_plan_has_required_fields(self):
        for code, meta in PLAN_CATALOG.items():
            for field in ("squad", "device_limit", "display", "prices", "features"):
                assert field in meta, f"plan {code!r} missing {field!r}"

    def test_legacy_codes_constant(self):
        assert LEGACY_PLAN_CODES == ("basic", "premium")

    def test_new_codes_constant(self):
        assert NEW_PLAN_CODES == ("lite", "standard", "pro")

    def test_plan_names_consistent_with_catalog(self):
        # Обратная совместимость со старым PLAN_NAMES dict.
        for code, name in PLAN_NAMES.items():
            assert PLAN_CATALOG[code]["display"] == name


class TestGetPlanSquad:
    def test_legacy(self):
        assert get_plan_squad("basic") == "basic"
        assert get_plan_squad("premium") == "premium"

    def test_new(self):
        assert get_plan_squad("lite") == "lite"
        assert get_plan_squad("standard") == "standard"
        assert get_plan_squad("pro") == "pro"

    def test_trial_routes_to_standard_squad(self):
        # Триал-юзеры (cohort=new) попадают в standard squad.
        assert get_plan_squad("trial") == "standard"

    def test_unknown_returns_none(self):
        assert get_plan_squad("unknown") is None
        assert get_plan_squad(None) is None


class TestGetPlanDeviceLimit:
    def test_legacy(self):
        assert get_plan_device_limit("basic") == 5
        assert get_plan_device_limit("premium") == 15

    def test_new(self):
        assert get_plan_device_limit("lite") == 2
        assert get_plan_device_limit("standard") == 5
        assert get_plan_device_limit("pro") == 10

    def test_unknown_returns_default_5(self):
        # Дефолт 5 чтобы не падать на старых записях с битым plan_code.
        assert get_plan_device_limit("unknown") == 5
        assert get_plan_device_limit(None) == 5


class TestGetPlanPrice:
    def test_legacy_basic(self):
        assert get_plan_price("basic", 1) == 99
        assert get_plan_price("basic", 3) == 249
        assert get_plan_price("basic", 6) == 499
        assert get_plan_price("basic", 12) == 899

    def test_legacy_premium(self):
        assert get_plan_price("premium", 1) == 199
        assert get_plan_price("premium", 12) == 1799

    def test_new_lite(self):
        assert get_plan_price("lite", 1) == 129
        assert get_plan_price("lite", 12) == 1099

    def test_new_standard(self):
        assert get_plan_price("standard", 1) == 249
        assert get_plan_price("standard", 6) == 1199
        assert get_plan_price("standard", 12) == 2199

    def test_new_pro(self):
        assert get_plan_price("pro", 1) == 449
        assert get_plan_price("pro", 12) == 3999

    def test_unknown_or_invalid_period_returns_zero(self):
        assert get_plan_price("basic", 99) == 0
        assert get_plan_price("unknown", 1) == 0
        assert get_plan_price(None, 1) == 0


class TestMenuPlanCodes:
    def test_menu_is_new_cohort(self):
        # Меню тарифов одно для всех — это новые тарифы.
        assert MENU_PLAN_CODES == NEW_PLAN_CODES

    def test_no_legacy_in_menu(self):
        for code in LEGACY_PLAN_CODES:
            assert code not in MENU_PLAN_CODES


class TestGetPlanFeatures:
    def test_returns_list_copy(self):
        features1 = get_plan_features("standard")
        features2 = get_plan_features("standard")
        assert features1 == features2
        # Это копии, мутация одной не должна задеть другую и каталог.
        features1.append("hacked")
        assert get_plan_features("standard") != features1

    def test_unknown_returns_empty(self):
        assert get_plan_features("unknown") == []
