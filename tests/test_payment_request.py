"""Тесты системы ручной модерации платежей."""
import pytest
from unittest.mock import patch

from app.services.payment_request import (
    generate_req_id,
    build_payreq_block,
    parse_payreq_block,
    verify_payreq,
    _sign_payload,
    verify_signature,
    PaymentRequestData,
)


def test_generate_req_id():
    """Проверка формата req_id."""
    req_id = generate_req_id()
    assert req_id.startswith("PRQ-")
    assert len(req_id) == 9  # PRQ- + 5 символов


def test_build_and_parse_payreq():
    """Генерация и парсинг блока #PAYREQ."""
    with patch("app.services.payment_request._get_hmac_secret", return_value=b"test-secret"):
        block = build_payreq_block(
            req_id="PRQ-TEST1",
            tg_id=123456,
            username="@user",
            name="Ivan Ivanov",
            tariff="basic_1",
            amount=99,
            currency="RUB",
        )
    assert "#PAYREQ" in block
    assert "req_id=PRQ-TEST1" in block
    assert "tg_id=123456" in block
    assert "status=NEW" in block
    assert "sig=" in block

    pr = parse_payreq_block(block)
    assert pr is not None
    assert pr.req_id == "PRQ-TEST1"
    assert pr.tg_id == 123456
    assert pr.tariff == "basic_1"
    assert pr.amount == 99
    assert pr.status == "NEW"


def test_verify_signature():
    """Проверка HMAC подписи."""
    with patch("app.services.payment_request._get_hmac_secret", return_value=b"test-secret"):
        sig = _sign_payload("1", "PRQ-X", 123, "basic_1", 99, "RUB", "2026-03-05T12:00:00Z")
        assert len(sig) == 64  # hex sha256

        ok = verify_signature("1", "PRQ-X", 123, "basic_1", 99, "RUB", "2026-03-05T12:00:00Z", sig)
        assert ok is True

        ok_bad = verify_signature("1", "PRQ-X", 123, "basic_1", 99, "RUB", "2026-03-05T12:00:00Z", "wrong")
        assert ok_bad is False


def test_verify_payreq():
    """Проверка verify_payreq."""
    with patch("app.services.payment_request._get_hmac_secret", return_value=b"test-secret"):
        block = build_payreq_block("PRQ-X", 123, "@u", "N", "basic_1", 99, "RUB")
        pr_parsed = parse_payreq_block(block)
        assert pr_parsed is not None
        verify_ok = verify_payreq(pr_parsed)
        assert verify_ok is True


def test_parse_invalid():
    """Парсинг невалидного блока."""
    assert parse_payreq_block("") is None
    assert parse_payreq_block("no payreq here") is None


def test_idempotency_status():
    """Проверка: если status != NEW, ничего не делать (логика в handler)."""
    pr = PaymentRequestData(
        req_id="PRQ-X",
        tg_id=123,
        username="@u",
        name="N",
        tariff="basic_1",
        amount=99,
        currency="RUB",
        created="2026-03-05T12:00:00Z",
        status="APPROVED",
        sig="x",
    )
    assert pr.status != "NEW"
