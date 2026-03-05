"""#PAYREQ блок: генерация, парсинг, HMAC."""
from .core import (
    generate_req_id,
    build_payreq_block,
    parse_payreq_block,
    verify_payreq,
    PaymentRequestData,
)

__all__ = [
    "generate_req_id",
    "build_payreq_block",
    "parse_payreq_block",
    "verify_payreq",
    "PaymentRequestData",
]
