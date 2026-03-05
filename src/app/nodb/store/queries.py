"""
Функции для работы с SQLite store.
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from .db import get_db
from app.logger import logger


# === Payment Requests ===

@dataclass
class PaymentRequest:
    """Заявка на оплату."""
    id: int
    req_id: str
    tg_id: int
    username: Optional[str]
    name: Optional[str]
    tariff: str
    amount: int
    currency: str
    status: str
    signature: str
    source: str
    created_at: str
    resolved_at: Optional[str]
    admin_id: Optional[int]


async def create_payment_request(
    req_id: str,
    tg_id: int,
    username: Optional[str],
    name: Optional[str],
    tariff: str,
    amount: int,
    currency: str,
    signature: str,
    source: str = "card",
) -> int:
    """Создает новую заявку на оплату. Возвращает ID."""
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO payment_requests
            (req_id, tg_id, username, name, tariff, amount, currency, status, signature, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'NEW', ?, ?, ?)
            """,
            (req_id, tg_id, username, name, tariff, amount, currency, signature, source, created_at),
        )
        await db.commit()
        return cursor.lastrowid


async def get_payment_request_by_req_id(req_id: str) -> Optional[PaymentRequest]:
    """Получает заявку по req_id."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM payment_requests WHERE req_id = ?",
            (req_id,),
        )
        row = await cursor.fetchone()
        if row:
            return PaymentRequest(**dict(row))
        return None


async def get_pending_request_by_tg_id(tg_id: int, max_age_seconds: int = 120) -> Optional[PaymentRequest]:
    """Получает незавершенную заявку пользователя (для anti-spam)."""
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_seconds
    cutoff_str = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM payment_requests
            WHERE tg_id = ? AND status = 'NEW' AND created_at > ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (tg_id, cutoff_str),
        )
        row = await cursor.fetchone()
        if row:
            return PaymentRequest(**dict(row))
        return None


async def update_payment_request_status(
    req_id: str,
    status: str,
    admin_id: Optional[int] = None,
) -> bool:
    """Обновляет статус заявки. Возвращает True если обновлено."""
    resolved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async with get_db() as db:
        cursor = await db.execute(
            """
            UPDATE payment_requests
            SET status = ?, resolved_at = ?, admin_id = ?
            WHERE req_id = ? AND status = 'NEW'
            """,
            (status, resolved_at, admin_id, req_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_recent_payment_requests(limit: int = 10) -> List[PaymentRequest]:
    """Получает последние N заявок."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM payment_requests
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [PaymentRequest(**dict(row)) for row in rows]


# === Payment Events (audit log) ===

async def log_event(
    event_type: str,
    req_id: Optional[str] = None,
    tg_id: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    """Записывает событие в аудит-лог. Возвращает ID."""
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload_json = json.dumps(payload or {}, ensure_ascii=False)

    async with get_db() as db:
        cursor = await db.execute(
            """
            INSERT INTO payment_events (req_id, tg_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (req_id, tg_id, event_type, payload_json, created_at),
        )
        await db.commit()
        return cursor.lastrowid


async def get_events_by_req_id(req_id: str) -> List[Dict[str, Any]]:
    """Получает все события для req_id."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM payment_events WHERE req_id = ?
            ORDER BY created_at ASC
            """,
            (req_id,),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("payload_json"):
                try:
                    d["payload"] = json.loads(d["payload_json"])
                except json.JSONDecodeError:
                    d["payload"] = {}
            result.append(d)
        return result


# === Promo Activations ===

async def has_promo_activation(tg_id: int, promo_code: str) -> bool:
    """Проверяет, активировал ли пользователь данный промокод."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT 1 FROM promo_activations WHERE tg_id = ? AND promo_code = ?",
            (tg_id, promo_code),
        )
        row = await cursor.fetchone()
        return row is not None


async def record_promo_activation(tg_id: int, promo_code: str) -> bool:
    """Записывает активацию промокода. Возвращает False если уже активирован."""
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async with get_db() as db:
        try:
            await db.execute(
                """
                INSERT INTO promo_activations (tg_id, promo_code, created_at)
                VALUES (?, ?, ?)
                """,
                (tg_id, promo_code, created_at),
            )
            await db.commit()
            return True
        except Exception as e:
            # UNIQUE constraint failed
            if "UNIQUE" in str(e):
                return False
            raise


# === Webhook Events (idempotency) ===

async def check_webhook_processed(external_id: str, event_type: str) -> bool:
    """Проверяет, обработан ли уже этот webhook."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT processed FROM webhook_events
            WHERE external_id = ? AND event_type = ?
            """,
            (external_id, event_type),
        )
        row = await cursor.fetchone()
        if row:
            return bool(row["processed"])
        return False


async def check_webhook_provisioned(external_id: str) -> bool:
    """Проверяет, был ли уже provision для этого платежа."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT provisioned FROM webhook_events
            WHERE external_id = ? AND provisioned = 1
            """,
            (external_id,),
        )
        row = await cursor.fetchone()
        return row is not None


async def record_webhook_event(
    external_id: str,
    event_type: str,
    status: Optional[str] = None,
    tg_id: Optional[int] = None,
    amount: Optional[float] = None,
    processed: bool = False,
    provisioned: bool = False,
) -> bool:
    """
    Записывает webhook событие.
    Использует INSERT OR REPLACE для идемпотентности.
    Возвращает True если это новая запись.
    """
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async with get_db() as db:
        # Проверяем существует ли
        cursor = await db.execute(
            "SELECT id, provisioned FROM webhook_events WHERE external_id = ? AND event_type = ?",
            (external_id, event_type),
        )
        existing = await cursor.fetchone()

        if existing:
            # Обновляем, но не сбрасываем provisioned если уже True
            old_provisioned = bool(existing["provisioned"])
            new_provisioned = old_provisioned or provisioned

            await db.execute(
                """
                UPDATE webhook_events
                SET status = ?, tg_id = ?, amount = ?, processed = ?, provisioned = ?
                WHERE id = ?
                """,
                (status, tg_id, amount, int(processed), int(new_provisioned), existing["id"]),
            )
            await db.commit()
            return False
        else:
            await db.execute(
                """
                INSERT INTO webhook_events
                (external_id, event_type, status, tg_id, amount, processed, provisioned, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (external_id, event_type, status, tg_id, amount, int(processed), int(provisioned), created_at),
            )
            await db.commit()
            return True


async def mark_webhook_provisioned(external_id: str) -> None:
    """Помечает платеж как provisioned (доступ выдан)."""
    async with get_db() as db:
        await db.execute(
            "UPDATE webhook_events SET provisioned = 1 WHERE external_id = ?",
            (external_id,),
        )
        await db.commit()
