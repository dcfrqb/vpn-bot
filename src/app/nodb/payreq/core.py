"""
#PAYREQ блок: генерация, парсинг, HMAC-SHA256.
Подписываем: v|req_id|tg_id|tariff|amount|currency|created
"""
import hashlib
import hmac
import os
import re
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.config import settings
from app.logger import logger


def generate_req_id() -> str:
    """Генерирует уникальный ID заявки: PRQ-XXXXX"""
    chars = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(chars) for _ in range(5))
    return f"PRQ-{suffix}"


def _get_hmac_secret() -> bytes:
    secret = getattr(settings, "PAYREQ_HMAC_SECRET", None) or getattr(
        settings, "payreq_hmac_secret", None
    )
    if not secret or not str(secret).strip():
        if os.environ.get("PREFLIGHT_DEV") == "1" or not os.environ.get("PREFLIGHT_DOCKER"):
            return b"dev-secret-change-in-production"
        raise ValueError("PAYREQ_HMAC_SECRET не настроен")
    return str(secret).strip().encode("utf-8")


def _sign_payload(v: str, req_id: str, tg_id: int, tariff: str, amount: int, currency: str, created: str) -> str:
    """Подписывает строку: v|req_id|tg_id|tariff|amount|currency|created"""
    payload = f"{v}|{req_id}|{int(tg_id)}|{tariff}|{int(amount)}|{currency}|{created}"
    sig = hmac.new(_get_hmac_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return sig


def verify_signature(
    v: str, req_id: str, tg_id: int, tariff: str, amount: int, currency: str, created: str, sig: str
) -> bool:
    """Проверяет HMAC подпись"""
    try:
        expected = _sign_payload(v, req_id, tg_id, tariff, amount, currency, created)
        return hmac.compare_digest(expected, sig)
    except Exception as e:
        logger.warning(f"Ошибка проверки подписи PAYREQ: {e}")
        return False


@dataclass
class PaymentRequestData:
    req_id: str
    tg_id: int
    username: str
    name: str
    tariff: str
    amount: int
    currency: str
    created: str
    status: str
    sig: str
    admin_id: Optional[int] = None
    resolved: Optional[str] = None


def build_payreq_block(
    req_id: str,
    tg_id: int,
    username: str,
    name: str,
    tariff: str,
    amount: int,
    currency: str,
) -> str:
    """Формирует блок #PAYREQ для вставки в сообщение админу."""
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    v = "1"
    sig = _sign_payload(v, req_id, tg_id, tariff, amount, currency, created)

    lines = [
        "#PAYREQ",
        f"v={v}",
        f"req_id={req_id}",
        f"tg_id={tg_id}",
        f"username={username}",
        f"name={name}",
        f"tariff={tariff}",
        f"amount={amount}",
        f"currency={currency}",
        f"created={created}",
        "status=NEW",
        f"sig={sig}",
    ]
    return "\n".join(lines)


def parse_payreq_block(text: str) -> Optional[PaymentRequestData]:
    """
    Парсит блок #PAYREQ из текста сообщения.
    Возвращает PaymentRequestData или None при ошибке.
    """
    match = re.search(r"#PAYREQ\s*\n(.*?)(?=\n#|\n\n\n|\Z)", text, re.DOTALL)
    if not match:
        return None

    block = match.group(1).strip()
    data = {}
    for line in block.split("\n"):
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()

    try:
        req_id = data.get("req_id")
        tg_id = int(data.get("tg_id", 0))
        username = data.get("username", "")
        name = data.get("name", "")
        tariff = data.get("tariff", "")
        amount = int(data.get("amount", 0))
        currency = data.get("currency", "RUB")
        created = data.get("created", "")
        status = data.get("status", "NEW")
        sig = data.get("sig", "")
        admin_id = data.get("admin_id")
        resolved = data.get("resolved")

        if not req_id or not sig:
            return None

        return PaymentRequestData(
            req_id=req_id,
            tg_id=tg_id,
            username=username,
            name=name,
            tariff=tariff,
            amount=amount,
            currency=currency,
            created=created,
            status=status,
            sig=sig,
            admin_id=int(admin_id) if admin_id and admin_id.isdigit() else None,
            resolved=resolved,
        )
    except (ValueError, KeyError) as e:
        logger.warning(f"Ошибка парсинга PAYREQ: {e}")
        return None


def verify_payreq(pr: PaymentRequestData) -> bool:
    """Проверяет HMAC подписи заявки."""
    return verify_signature(
        "1", pr.req_id, int(pr.tg_id), pr.tariff, int(pr.amount), pr.currency, pr.created, pr.sig
    )
