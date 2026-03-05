"""
Append-only JSONL логирование.
Единственное локальное хранилище данных бота.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings
from app.logger import logger as app_logger

LOG_DIR: Optional[Path] = None


def _get_log_dir() -> Path:
    global LOG_DIR
    if LOG_DIR is not None:
        return LOG_DIR
    log_dir = getattr(settings, "LOG_DIR", None)
    if log_dir:
        p = Path(log_dir)
    else:
        p = Path(__file__).resolve().parents[3] / "logs"
    p.mkdir(parents=True, exist_ok=True)
    LOG_DIR = p
    return p


def _ensure_no_secrets(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Удаляет секреты из payload перед логированием."""
    forbidden = {"token", "secret", "password", "key", "api_key"}
    result = {}
    for k, v in payload.items():
        if any(f in k.lower() for f in forbidden) and isinstance(v, str):
            result[k] = "[REDACTED]"
        elif isinstance(v, dict):
            result[k] = _ensure_no_secrets(v)
        else:
            result[k] = v
    return result


def _write_jsonl(filename: str, record: Dict[str, Any]) -> None:
    """Записывает одну строку JSON в файл."""
    try:
        log_dir = _get_log_dir()
        path = log_dir / filename
        record["ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        safe_payload = _ensure_no_secrets(record.get("payload", {}))
        if safe_payload != record.get("payload"):
            record["payload"] = safe_payload
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        app_logger.error(f"Ошибка записи в JSONL {filename}: {e}")


def log_bot_event(event: str, payload: Optional[Dict[str, Any]] = None, **kwargs) -> None:
    """Записывает событие в logs/bot_events.jsonl"""
    record = {"event": event, "payload": payload or {}}
    record.update(kwargs)
    _write_jsonl("bot_events.jsonl", record)


def log_payment_event(
    event: str,
    req_id: Optional[str] = None,
    tg_id: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
    **kwargs
) -> None:
    """Записывает событие платежа в logs/payments.jsonl"""
    record = {
        "event": event,
        "req_id": req_id,
        "tg_id": tg_id,
        "payload": payload or {},
    }
    record.update(kwargs)
    _write_jsonl("payments.jsonl", record)


# Типы событий для payments.jsonl
EVENT_PAYMENT_REQUEST_CREATED = "payment_request_created"
EVENT_ADMIN_NOTIFIED = "admin_notified"
EVENT_PAYMENT_APPROVED = "payment_approved"
EVENT_PAYMENT_REJECTED = "payment_rejected"
EVENT_USER_NOTIFIED = "user_notified"
EVENT_REMNAWAVE_PROVISION_SUCCESS = "remnawave_provision_success"
EVENT_REMNAWAVE_PROVISION_FAILED = "remnawave_provision_failed"
EVENT_TELEGRAM_ERROR = "telegram_error"
