"""Smoke-тесты для маппинга tariff → (plan_code, period) и
plan_code → (squad, device_limit).

Чисто статические проверки против справочников — без сети/БД.
Защищают от того, чтобы случайно сломать legacy на рефакторинге каталога.
"""
from __future__ import annotations

import pytest

from app.core.plans import (
    LEGACY_PLAN_CODES,
    NEW_PLAN_CODES,
    get_plan_device_limit,
    get_plan_squad,
)
from app.services.remna_service import TARIFF_TO_DAYS, TARIFF_TO_PLAN


class TestTariffToPlan:
    """TARIFF_TO_PLAN: legacy сохранён, new добавлен, period корректен."""

    @pytest.mark.parametrize("tariff,expected_plan,expected_months", [
        # legacy uppercase (история)
        ("PRO_1M", "premium", 1),
        ("PRO_12M", "premium", 12),
        ("BASIC_1M", "basic", 1),
        # legacy lowercase
        ("basic_1", "basic", 1),
        ("basic_12", "basic", 12),
        ("premium_1", "premium", 1),
        ("premium_forever", "premium", -1),
        # new cohort
        ("lite_1", "lite", 1),
        ("lite_3", "lite", 3),
        ("lite_6", "lite", 6),
        ("lite_12", "lite", 12),
        ("standard_1", "standard", 1),
        ("standard_12", "standard", 12),
        ("pro_1", "pro", 1),
        ("pro_12", "pro", 12),
        ("pro_forever", "pro", -1),
    ])
    def test_tariff_resolves(self, tariff, expected_plan, expected_months):
        assert TARIFF_TO_PLAN[tariff] == (expected_plan, expected_months)

    def test_legacy_tariffs_intact(self):
        """Защита от регрессии: все legacy ключи на месте, ничего не подменилось."""
        for legacy_key in (
            "PRO_1M", "PRO_3M", "PRO_6M", "PRO_12M",
            "BASIC_1M", "BASIC_3M", "BASIC_6M", "BASIC_12M",
            "basic_1", "basic_3", "basic_6", "basic_12",
            "premium_1", "premium_3", "premium_6", "premium_12",
            "premium_forever",
        ):
            assert legacy_key in TARIFF_TO_PLAN, f"legacy tariff {legacy_key!r} пропал"
            plan, _ = TARIFF_TO_PLAN[legacy_key]
            assert plan in LEGACY_PLAN_CODES


class TestTariffToDays:
    """TARIFF_TO_DAYS: legacy trial_10d/solokhin_15d сохранены, новый trial добавлен."""

    def test_legacy_trial_keeps_basic_squad(self):
        plan, days = TARIFF_TO_DAYS["trial_10d"]
        assert plan == "basic"
        assert days == 10

    def test_legacy_solokhin_keeps_premium_squad(self):
        plan, days = TARIFF_TO_DAYS["solokhin_15d"]
        assert plan == "premium"
        assert days == 15

    def test_new_trial_routes_to_standard(self):
        plan, days = TARIFF_TO_DAYS["trial_standard_10d"]
        assert plan == "standard"
        assert days == 10


class TestPlanCodeToSquadAndLimit:
    """Все plan_codes которые могут попасть в provision_tariff должны иметь squad+limit."""

    @pytest.mark.parametrize("plan_code,expected_squad,expected_limit", [
        # legacy
        ("basic", "basic", 5),
        ("premium", "premium", 15),
        # new
        ("lite", "lite", 2),
        ("standard", "standard", 5),
        ("pro", "pro", 10),
    ])
    def test_squad_and_limit(self, plan_code, expected_squad, expected_limit):
        assert get_plan_squad(plan_code) == expected_squad
        assert get_plan_device_limit(plan_code) == expected_limit

    def test_unknown_plan_safe_defaults(self):
        # Защита от мусора в БД — provision не должен крашиться, должен идти fallback.
        assert get_plan_squad("unknown_garbage") is None  # caller получит предсказуемый None
        assert get_plan_device_limit("unknown_garbage") == 5  # дефолт = 5


class TestNoOverlapBetweenCohorts:
    """LEGACY и NEW коды не должны пересекаться."""

    def test_disjoint_plan_codes(self):
        assert set(LEGACY_PLAN_CODES).isdisjoint(set(NEW_PLAN_CODES))
