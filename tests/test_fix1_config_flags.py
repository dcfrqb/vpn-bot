"""Фикс-раунд 1: терпимый разбор PROMO_*/TASK_* флагов (старт не падает на
странном значении) и выключатели фоновых задач для отладочного бота."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings, parse_bool


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), ("True", True), ("1", True), ("yes", True), ("On", True),
    ("false", False), ("False", False), ("0", False), ("NO", False), ("off", False),
    (" false ", False), ('"false"', False),
])
def test_promo_flags_accept_common_spellings(monkeypatch, raw, expected):
    monkeypatch.setenv("PROMO_TRIAL_ENABLED", raw)
    monkeypatch.setenv("PROMO_SOLOKHIN_ENABLED", raw)
    s = Settings(_env_file=None)
    assert s.PROMO_TRIAL_ENABLED is expected
    assert s.PROMO_SOLOKHIN_ENABLED is expected


@pytest.mark.parametrize("raw", ["maybe", "", "2", "выкл", "fasle", "0ff"])
def test_invalid_flag_does_not_crash(monkeypatch, raw):
    """PROMO_* при непонятном значении берут дефолт, а выключатели фоновых
    задач выключаются (фикс-раунд 2, ревью N5: fail safe)."""
    monkeypatch.setenv("PROMO_ADMIN_ENABLED", raw)
    monkeypatch.setenv("BACKGROUND_TASKS_ENABLED", raw)
    monkeypatch.setenv("TASK_RECOVERY_ENABLED", raw)
    s = Settings(_env_file=None)  # раньше ValidationError клал оба контейнера
    assert s.PROMO_ADMIN_ENABLED is True
    assert s.BACKGROUND_TASKS_ENABLED is False
    assert s.TASK_RECOVERY_ENABLED is False


def test_parse_bool_unknown_is_none():
    assert parse_bool("maybe") is None
    assert parse_bool(None) is None


@pytest.mark.parametrize("master,task,expected", [
    (True, True, True), (True, False, False), (False, True, False),
])
def test_task_enabled_master_and_per_task(master, task, expected):
    from app import config

    with patch.object(config.settings, "BACKGROUND_TASKS_ENABLED", master), \
         patch.object(config.settings, "TASK_RECONCILER_ENABLED", task):
        assert config.task_enabled("RECONCILER") is expected


@pytest.mark.asyncio
async def test_background_tasks_all_off_starts_nothing():
    from app import config
    from app.tasks import background as main

    checker_cls = MagicMock()
    checker_cls.any_stage_enabled = MagicMock(return_value=False)
    sun_cls = MagicMock()
    resume = AsyncMock()
    with patch.object(config.settings, "BACKGROUND_TASKS_ENABLED", False), \
         patch("app.tasks.subscription_checker.SubscriptionChecker.any_stage_enabled", return_value=False), \
         patch("app.tasks.subscription_checker.SubscriptionChecker.start") as checker_start, \
         patch("app.tasks.sun718_revert.Sun718RevertTask.start") as sun_start, \
         patch("app.services.broadcast.resume_unfinished_broadcasts", resume):
        handle = await main.start_background_tasks(MagicMock())
    checker_start.assert_not_called()
    sun_start.assert_not_called()
    resume.assert_not_awaited()
    handle.stop()  # заглушка со stop()


@pytest.mark.asyncio
async def test_checker_runs_only_enabled_stages():
    from app import config
    from app.tasks.subscription_checker import SubscriptionChecker

    checker = SubscriptionChecker(MagicMock())
    with patch.object(config.settings, "BACKGROUND_TASKS_ENABLED", True), \
         patch.object(config.settings, "TASK_RECOVERY_ENABLED", False), \
         patch.object(config.settings, "TASK_EXPIRY_NOTIFIER_ENABLED", True), \
         patch.object(config.settings, "TASK_RECONCILER_ENABLED", False), \
         patch.object(checker, "_run_recovery", AsyncMock()) as rec, \
         patch.object(checker, "_run_expiry", AsyncMock()) as exp, \
         patch.object(checker, "_run_reconciler", AsyncMock()) as recon:
        await checker._run_once("t")
    rec.assert_not_awaited()
    exp.assert_awaited_once()
    recon.assert_not_awaited()
