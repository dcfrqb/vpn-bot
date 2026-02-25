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
            os.environ["DATABASE_URL"] = "postgresql://u:p@h/d"
            os.environ["REDIS_URL"] = "redis://localhost/0"
            os.environ["YOOKASSA_SHOP_ID"] = "shop"
            os.environ["YOOKASSA_API_KEY"] = "key"
            os.environ["REMNA_API_BASE"] = "https://api.example.com"
            os.environ["REMNA_API_KEY"] = "remna_key"
            # BOT_TOKEN отсутствует

            with pytest.raises(SystemExit) as exc_info:
                run_preflight(in_docker=True)

            assert "BOT_TOKEN" in str(exc_info.value)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_preflight_fails_without_database_url_in_docker(self):
        """В Docker без DATABASE_URL — SystemExit с сообщением о DATABASE_URL."""
        from app.utils.preflight import run_preflight

        env_backup = os.environ.copy()
        try:
            os.environ.clear()
            os.environ["BOT_TOKEN"] = "token"
            os.environ["REDIS_URL"] = "redis://localhost/0"
            os.environ["YOOKASSA_SHOP_ID"] = "shop"
            os.environ["YOOKASSA_API_KEY"] = "key"
            os.environ["REMNA_API_BASE"] = "https://api.example.com"
            os.environ["REMNA_API_KEY"] = "remna_key"
            # DATABASE_URL отсутствует

            with pytest.raises(SystemExit) as exc_info:
                run_preflight(in_docker=True)

            assert "DATABASE_URL" in str(exc_info.value)
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
            os.environ["DATABASE_URL"] = "postgresql://u:p@h/d"
            os.environ["REDIS_URL"] = "redis://localhost/0"
            os.environ["YOOKASSA_SHOP_ID"] = "shop"
            os.environ["YOOKASSA_API_KEY"] = "key"
            os.environ["REMNA_API_BASE"] = "https://api.example.com"
            os.environ["REMNA_API_KEY"] = "remna_key"

            run_preflight(in_docker=True)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)

    def test_preflight_dev_skips_db_redis_requirement(self):
        """PREFLIGHT_DEV=1 — DATABASE_URL и REDIS_URL не обязательны."""
        from app.utils.preflight import run_preflight

        env_backup = os.environ.copy()
        try:
            os.environ.clear()
            os.environ["PREFLIGHT_DEV"] = "1"
            os.environ["BOT_TOKEN"] = "token"
            os.environ["YOOKASSA_SHOP_ID"] = "shop"
            os.environ["YOOKASSA_API_KEY"] = "key"
            os.environ["REMNA_API_BASE"] = "https://api.example.com"
            os.environ["REMNA_API_KEY"] = "remna_key"
            # DATABASE_URL, REDIS_URL отсутствуют

            run_preflight(in_docker=False)
        finally:
            os.environ.clear()
            os.environ.update(env_backup)
