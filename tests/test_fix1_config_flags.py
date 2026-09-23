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
    """BACKGROUND_TASKS_ENABLED=false (debug bot): the scheduler starts no job at all."""
    from app import config
    from app.worker.scheduler import Scheduler, build_jobs

    class Leader:
        async def ensure(self):
            return True

        async def release(self):
            pass

    with patch.object(config.settings, "BACKGROUND_TASKS_ENABLED", False):
        s = Scheduler(build_jobs(MagicMock()), leader=Leader(), tick_s=3600)
        await s.start()
        started = await s.tick()
        s.stop()
    assert started == []
    assert all(st.runs == 0 for st in s.state.values())
