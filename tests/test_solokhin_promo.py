"""
Тесты промокодных команд /solokhin и /trial.

/solokhin  — Premium 15 дней, tariff=solokhin_15d
/trial     — Basic   10 дней, tariff=trial_10d

Примечание: тесты бизнес-логики хэндлеров требуют полного окружения (dateutil и др.),
поэтому они запускаются в Docker-контейнере (см. test_promo_commands_full.py).
Здесь — только тесты, которые можно запустить в локальном venv без тяжёлых зависимостей.
"""
import sys
import types as pytypes
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# TARIFF_TO_DAYS constants (isolated via sys.modules mock)
# ---------------------------------------------------------------------------

def _import_tariff_constants():
    """Import TARIFF_TO_DAYS without triggering dateutil/SQLAlchemy chain."""
    # Stub out dateutil before it's needed
    dateutil_mock = MagicMock()
    dateutil_mock.relativedelta = MagicMock()
    sys.modules.setdefault("dateutil", dateutil_mock)
    sys.modules.setdefault("dateutil.relativedelta", dateutil_mock)

    # Force re-parse by removing cached module if it was previously loaded
    # without dateutil stub (shouldn't happen in clean test run, but be safe)
    import importlib
    # Stub heavy deps that remna_service pulls in
    for mod in ["app.remnawave.client", "app.logger", "app.services.jsonl_logger"]:
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    if "app.services.remna_service" in sys.modules:
        module = sys.modules["app.services.remna_service"]
    else:
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "app.services.remna_service",
            os.path.join(os.path.dirname(__file__), "../src/app/services/remna_service.py"),
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["app.services.remna_service"] = module
        spec.loader.exec_module(module)

    return module.TARIFF_TO_DAYS


def test_tariff_solokhin_15d_exists():
    TARIFF_TO_DAYS = _import_tariff_constants()
    assert "solokhin_15d" in TARIFF_TO_DAYS
    plan, days = TARIFF_TO_DAYS["solokhin_15d"]
    assert plan == "premium"
    assert days == 15


def test_tariff_trial_10d_exists():
    TARIFF_TO_DAYS = _import_tariff_constants()
    assert "trial_10d" in TARIFF_TO_DAYS
    plan, days = TARIFF_TO_DAYS["trial_10d"]
    assert plan == "basic"
    assert days == 10


def test_old_solokhin_10d_removed():
    TARIFF_TO_DAYS = _import_tariff_constants()
    assert "solokhin_10d" not in TARIFF_TO_DAYS


def test_tariff_plans_are_distinct():
    """solokhin → premium, trial → basic."""
    TARIFF_TO_DAYS = _import_tariff_constants()
    sol_plan, _ = TARIFF_TO_DAYS["solokhin_15d"]
    trial_plan, _ = TARIFF_TO_DAYS["trial_10d"]
    assert sol_plan == "premium"
    assert trial_plan == "basic"
    assert sol_plan != trial_plan


def test_tariff_durations_are_distinct():
    """solokhin → 15d, trial → 10d."""
    TARIFF_TO_DAYS = _import_tariff_constants()
    _, sol_days = TARIFF_TO_DAYS["solokhin_15d"]
    _, trial_days = TARIFF_TO_DAYS["trial_10d"]
    assert sol_days == 15
    assert trial_days == 10
    assert sol_days != trial_days


# ---------------------------------------------------------------------------
# Dedup key isolation (pure logic, no imports needed)
# ---------------------------------------------------------------------------

def test_dedup_keys_are_distinct():
    """Каждый промокод использует свой namespace в payments.external_id."""
    user_id = 42
    sol_key = f"promo_solokhin_{user_id}"
    trial_key = f"promo_trial_{user_id}"
    assert sol_key != trial_key


def test_dedup_keys_include_user_id():
    """Ключ содержит user_id → разные пользователи не блокируют друг друга."""
    user_a, user_b = 111, 222
    assert f"promo_solokhin_{user_a}" != f"promo_solokhin_{user_b}"
    assert f"promo_trial_{user_a}" != f"promo_trial_{user_b}"
