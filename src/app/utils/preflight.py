"""
Preflight проверка обязательных переменных окружения перед стартом.
Используется в bot и webhook-api для раннего падения с понятной ошибкой.
"""
import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

# Загрузка .env для локального запуска (в Docker переменные уже в os.environ)
_env_path = Path("/opt/crs-vpn-bot/.env") if Path("/opt/crs-vpn-bot/.env").exists() else Path(__file__).resolve().parents[3] / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

from app.logger import logger


def _get(name: str) -> Optional[str]:
    """Получить значение env, пустая строка считается отсутствующей."""
    v = os.environ.get(name)
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return v.strip()


def _in_docker() -> bool:
    """Определяет, запущено ли приложение в Docker."""
    if _get("PREFLIGHT_DEV") == "1":
        return False
    return _get("PREFLIGHT_DOCKER") == "1" or _get("DOCKER") == "1" or (os.path.exists("/.dockerenv") if os.name != "nt" else False)


def run_preflight(
    *,
    strict_env: bool = False,
    in_docker: Optional[bool] = None,
) -> None:
    """
    Проверяет обязательные переменные окружения.
    При отсутствии обязательной переменной — падает с понятной ошибкой.

    Args:
        strict_env: если True (STRICT_ENV=1), YOOKASSA_WEBHOOK_SECRET обязателен
        in_docker: если True, DATABASE_URL и REDIS_URL обязательны. None = автоопределение
    """
    errors: List[str] = []
    warnings: List[str] = []

    if in_docker is None:
        in_docker = _in_docker()

    # Всегда обязательные (в Docker)
    if in_docker:
        if not _get("DATABASE_URL"):
            errors.append("DATABASE_URL — обязателен в Docker (postgresql://user:pass@host:5432/dbname)")
        if not _get("REDIS_URL"):
            errors.append("REDIS_URL — обязателен в Docker (redis://redis:6379/0)")

    # Telegram
    if not _get("BOT_TOKEN"):
        errors.append("BOT_TOKEN — токен Telegram бота от @BotFather")

    # YooKassa (для платежей)
    if not _get("YOOKASSA_SHOP_ID"):
        errors.append("YOOKASSA_SHOP_ID — ID магазина YooKassa")
    if not _get("YOOKASSA_API_KEY"):
        errors.append("YOOKASSA_API_KEY — секретный ключ YooKassa")

    # Remna (VPN API)
    remna_base = _get("REMNA_API_BASE") or _get("REMNA_BASE_URL") or _get("remna_base_url")
    remna_key = _get("REMNA_API_KEY") or _get("REMNA_API_TOKEN") or _get("remna_api_token")
    if not remna_base:
        errors.append("REMNA_API_BASE — URL Remna API (https://your-remna-api.com)")
    if not remna_key:
        errors.append("REMNA_API_KEY — ключ доступа к Remna API")

    # Webhook mode
    if _get("TELEGRAM_WEBHOOK_URL"):
        # В webhook режиме TELEGRAM_WEBHOOK_URL обязателен — уже проверен
        pass

    # YOOKASSA_WEBHOOK_SECRET: warning в PROD, ошибка при STRICT_ENV
    if not _get("YOOKASSA_WEBHOOK_SECRET"):
        if strict_env:
            errors.append(
                "YOOKASSA_WEBHOOK_SECRET — обязателен при STRICT_ENV=1 "
                "(секрет для проверки webhook от YooKassa)"
            )
        else:
            warnings.append(
                "YOOKASSA_WEBHOOK_SECRET не задан — webhook принимает любые запросы (только для dev!)"
            )

    # ADMINS — желательно, но не блокируем старт
    if not _get("ADMINS") and not _get("admin_ids"):
        warnings.append("ADMINS не задан — административные команды недоступны")

    for w in warnings:
        logger.warning(f"Preflight: {w}")

    if errors:
        msg = "Preflight failed — отсутствуют обязательные переменные окружения:\n  - " + "\n  - ".join(errors)
        logger.error(msg)
        raise SystemExit(msg)


def run_preflight_webhook_api(
    *,
    strict_env: Optional[bool] = None,
    in_docker: bool = True,
) -> None:
    """
    Preflight для webhook-api сервиса.
    Те же проверки, что и для бота (webhook-api использует те же настройки).
    """
    if strict_env is None:
        strict_env = _get("STRICT_ENV") == "1"
    run_preflight(strict_env=strict_env, in_docker=in_docker)


def run_preflight_bot(
    *,
    strict_env: Optional[bool] = None,
    in_docker: bool = True,
) -> None:
    """
    Preflight для bot сервиса.
    """
    if strict_env is None:
        strict_env = _get("STRICT_ENV") == "1"
    run_preflight(strict_env=strict_env, in_docker=in_docker)
