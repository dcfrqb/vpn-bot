"""3.0 Foundation: every 3.0 flag exists and defaults OFF."""
from app.config import Settings

OFF_BOOLS = [
    "AUTOPAY_ENABLED", "STARS_ENABLED", "GIFTS_ENABLED", "REFUND_24H_ENABLED", "PROMO_CODES_ENABLED",
    "DEVICES_UNLINK_ENABLED", "MAINTENANCE_AUTO_ENABLED", "GRACE_ENABLED",
    "TASK_DEVICE_CLEANUP_ENABLED", "TASK_OBHOD_LIFECYCLE_ENABLED", "TASK_AUTOPAY_ENABLED",
    "TASK_GRACE_ENABLED", "TASK_PANEL_HEALTH_ENABLED", "TASK_REMINDERS_ENABLED", "TASK_PANEL_SYNC_ENABLED",
]
UNSET = [
    "PANEL_WEBHOOK_SECRET", "GRACE_SQUAD", "ADMIN_CHAT_ID", "ADMIN_TOPIC_PAYMENTS", "ADMIN_TOPIC_REFUNDS",
    "ADMIN_TOPIC_PANEL", "ADMIN_TOPIC_ERRORS", "ADMIN_TOPIC_PROMO", "ADMIN_TOPIC_BROADCAST",
    "CONNECT_ARTICLE_URL", "PRIVACY_URL", "SUPPORT_HANDLE",
]
VALUES = {"STARS_RATE": 0.0, "GRACE_DAYS": 3, "GRACE_DAILY_GB": 5, "DEVICE_CLEANUP_DAYS": 30,
          "DEVICE_CLEANUP_DRY_RUN": True, "RECONCILER_INTERVAL_S": 600}


def test_defaults():
    fields = Settings.model_fields
    for name in OFF_BOOLS:
        assert fields[name].default is False, name
    for name in UNSET:
        assert fields[name].default is None, name
    for name, value in VALUES.items():
        assert fields[name].default == value, name


def test_env_parsing(monkeypatch):
    monkeypatch.setenv("TASK_GRACE_ENABLED", "fasle")  # typo -> kill switch stays OFF
    monkeypatch.setenv("DEVICE_CLEANUP_DRY_RUN", "maybe")  # unknown -> safe default (dry run)
    monkeypatch.setenv("ADMIN_CHAT_ID", "")
    monkeypatch.setenv("GRACE_DAYS", "")
    monkeypatch.setenv("ADMIN_TOPIC_ERRORS", "17")
    s = Settings(_env_file=None)
    assert s.TASK_GRACE_ENABLED is False and s.DEVICE_CLEANUP_DRY_RUN is True
    assert s.ADMIN_CHAT_ID is None and s.GRACE_DAYS == 3 and s.ADMIN_TOPIC_ERRORS == 17


def test_env_example_lists_every_3_0_setting():
    import pathlib

    text = (pathlib.Path(__file__).resolve().parents[1] / ".env.example").read_text()
    for name in OFF_BOOLS + UNSET + list(VALUES):
        assert f"\n{name}=" in text, name


def test_config_has_stream_sections():
    import inspect

    from app import config

    src = inspect.getsource(config)
    for header in ("3.0 Foundation", "3.0 Stream A", "3.0 Stream B", "3.0 Stream C", "3.0 Stream D",
                   "3.0 Stream E", "3.0 Stream F"):
        assert f"# --- {header}" in src, header
