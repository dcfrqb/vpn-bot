"""
SQLite store для no_db режима.
Заменяет JSONL как основное хранилище.
"""
from .db import init_db, get_db, close_db
from .queries import (
    # Payment requests
    PaymentRequest,
    create_payment_request,
    get_payment_request_by_req_id,
    get_pending_request_by_tg_id,
    update_payment_request_status,
    get_recent_payment_requests,
    # Events
    log_event,
    get_events_by_req_id,
    # Promo
    has_promo_activation,
    record_promo_activation,
    # Webhooks
    check_webhook_processed,
    check_webhook_provisioned,
    record_webhook_event,
    mark_webhook_provisioned,
)

__all__ = [
    "init_db",
    "get_db",
    "close_db",
    "PaymentRequest",
    "create_payment_request",
    "get_payment_request_by_req_id",
    "get_pending_request_by_tg_id",
    "update_payment_request_status",
    "get_recent_payment_requests",
    "log_event",
    "get_events_by_req_id",
    "has_promo_activation",
    "record_promo_activation",
    "check_webhook_processed",
    "check_webhook_provisioned",
    "record_webhook_event",
    "mark_webhook_provisioned",
]
