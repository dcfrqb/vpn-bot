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
    legacy: DATABASE_URL обязателен.
    no_db: PAYREQ_HMAC_SECRET, ADMIN_IDS, Remnawave обязательны; DATABASE_URL не проверяется.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if in_docker is None:
        in_docker = _in_docker()

    bot_mode = _get("BOT_MODE") or "legacy"

    # Telegram — всегда
    if not _get("BOT_TOKEN"):
        errors.append("BOT_TOKEN — токен Telegram бота от @BotFather")

    # Remnawave — всегда (и legacy, и no_db)
    remna_base = (
        _get("REMNA_API_BASE") or _get("REMNAWAVE_API_URL")
        or _get("REMNA_BASE_URL") or _get("remna_base_url")
    )
    remna_key = (
        _get("REMNA_API_KEY") or _get("REMNAWAVE_API_TOKEN")
        or _get("REMNA_API_TOKEN") or _get("remna_api_token")
    )
    if not remna_base:
        errors.append("REMNA_API_BASE или REMNAWAVE_API_URL — URL Remna API")
    if not remna_key:
        errors.append("REMNA_API_KEY или REMNAWAVE_API_TOKEN — ключ доступа к Remna API")

    if bot_mode == "legacy":
        # legacy: DATABASE_URL обязателен (в docker)
        if in_docker and not _get("DATABASE_URL"):
            errors.append("DATABASE_URL — обязателен в legacy режиме (PostgreSQL)")
        if not _get("YOOKASSA_SHOP_ID"):
            warnings.append("YOOKASSA_SHOP_ID не задан — платежи только вручную")
        if not _get("YOOKASSA_WEBHOOK_SECRET"):
            if strict_env:
                errors.append("YOOKASSA_WEBHOOK_SECRET — обязателен при STRICT_ENV=1")
            else:
                warnings.append("YOOKASSA_WEBHOOK_SECRET не задан (только для dev!)")
    else:
        # no_db: DATABASE_URL НЕ проверяется
        if not _get("ADMINS") and not _get("admin_ids"):
            errors.append("ADMINS или ADMIN_IDS — обязательны в no_db режиме")
        if not _get("PAYREQ_HMAC_SECRET") and not _get("payreq_hmac_secret"):
            if in_docker or strict_env:
                errors.append("PAYREQ_HMAC_SECRET — обязателен в no_db режиме (prod)")
            else:
                warnings.append("PAYREQ_HMAC_SECRET не задан — dev-секрет (только для разработки!)")

    if in_docker and not _get("REDIS_URL"):
        warnings.append("REDIS_URL не задан — FSM в памяти (при рестарте состояние теряется)")

    if bot_mode == "legacy" and (not _get("ADMINS") and not _get("admin_ids")):
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
