"""Stream B jobs run through the scheduler context with container ports."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.worker.jobs import device_cleanup, obhod_lifecycle, panel_sync
from app.worker.scheduler import JobContext


def ctx(gw, notifier):
    return JobContext(bot=None, container=SimpleNamespace(remna=gw, notifier=notifier), label="periodic")


async def test_device_cleanup_job_uses_settings(gw, fake, notifier):
    fake.add_user(1, "u", telegram_id=1)
    fake.add_device(1, "HWID-OLD-00000001", updated="2020-01-01T00:00:00Z")
    with patch("app.config.settings.DEVICE_CLEANUP_DRY_RUN", True), patch("app.config.settings.DEVICE_CLEANUP_DAYS", 30):
        await device_cleanup.run(ctx(gw, notifier))
    assert fake.devices[1] and notifier.sent  # dry run: counted, reported, nothing deleted
    with patch("app.config.settings.DEVICE_CLEANUP_DRY_RUN", False):
        await device_cleanup.run(ctx(gw, notifier))
    assert fake.devices[1] == []


async def test_panel_sync_and_obhod_jobs_use_the_sql_repo(gw, notifier, repo):
    with patch("app.services.panel_sync.SqlAccountsRepo", return_value=repo), \
            patch("app.services.obhod.SqlAccountsRepo", return_value=repo):
        await panel_sync.run(ctx(gw, notifier))
        await obhod_lifecycle.run(ctx(gw, notifier))


def test_default_flags_are_off():
    from app.config import Settings

    d = {k: f.default for k, f in Settings.model_fields.items()}
    assert not d["TASK_PANEL_SYNC_ENABLED"] and not d["TASK_DEVICE_CLEANUP_ENABLED"]
    assert not d["TASK_OBHOD_LIFECYCLE_ENABLED"] and d["DEVICE_CLEANUP_DRY_RUN"] and not d["DEVICES_UNLINK_ENABLED"]
