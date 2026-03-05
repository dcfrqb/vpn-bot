"""Тесты preflight: проверка env переменных при старте."""
import os
import pytest


class TestPreflight:
    """Preflight падает с понятной ошибкой при отсутствии обязательных env."""

    def test_preflight_fails_without_bot_token(self):
        """Без BOT_TOKEN — SystemExit с сообщением о BOT_TOKEN."""
        from app.utils.preflight import run_preflight

        env_backup = os.environ.copy()
        try:
            os.environ.clear()
            os.environ["REDIS_URL"] = "redis://localhost/0"
            os.environ["REMNA_API_BASE"] = "https://api.example.com"
            os.environ["REMNA_API_KEY"] = "remna_key"
            # BOT_TOKEN отсутствует

            with pytest.raises(SystemExit) as exc_info:
                run_preflight(in_docker=True)

            assert "BOT_TOKEN" in str(exc_info.value)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_preflight_fails_without_remna_in_docker(self):
        """В Docker без REMNA_API_BASE — SystemExit с сообщением о Remna."""
        from app.utils.preflight import run_preflight

        env_backup = os.environ.copy()
        try:
            os.environ.clear()
            os.environ["BOT_TOKEN"] = "token"
            os.environ["REDIS_URL"] = "redis://localhost/0"
            os.environ["REMNA_API_KEY"] = "remna_key"
            # REMNA_API_BASE отсутствует

            with pytest.raises(SystemExit) as exc_info:
                run_preflight(in_docker=True)

            assert "REMNA" in str(exc_info.value) or "Remna" in str(exc_info.value)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_preflight_passes_with_all_required(self):
        """При всех обязательных переменных — не падает."""
        from app.utils.preflight import run_preflight

        env_backup = os.environ.copy()
        try:
            os.environ.clear()
            os.environ["BOT_TOKEN"] = "token"
            os.environ["REDIS_URL"] = "redis://localhost/0"
            os.environ["REMNA_API_BASE"] = "https://api.example.com"
            os.environ["REMNA_API_KEY"] = "remna_key"
            os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost/db"

            run_preflight(in_docker=True)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_preflight_dev_skips_redis_requirement(self):
        """PREFLIGHT_DEV=1 — REDIS_URL не обязателен."""
        from app.utils.preflight import run_preflight

        env_backup = os.environ.copy()
        try:
            os.environ.clear()
            os.environ["PREFLIGHT_DEV"] = "1"
            os.environ["BOT_TOKEN"] = "token"
            os.environ["REMNA_API_BASE"] = "https://api.example.com"
            os.environ["REMNA_API_KEY"] = "remna_key"

            run_preflight(in_docker=False)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)
