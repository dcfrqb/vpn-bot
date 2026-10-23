"""Accepted pending refund must not be presented as money already returned."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.callbacks import AdmRefund
from app.bot.routers.admin import payments


@pytest.mark.parametrize("code,expected", [
    ("pending_money", "обрабатывает возврат"),
    ("retry_access", "повторит попытку автоматически"),
    ("unknown_money", "требует проверки"),
])
async def test_pending_money_decision_is_explicit(monkeypatch, code, expected):
    decision = AsyncMock(return_value=code)
    monkeypatch.setattr(payments, "money", lambda _: SimpleNamespace(refunds=SimpleNamespace(decide=decision)))
    message = SimpleNamespace(text="Запрос", html_text="Запрос", edit_text=AsyncMock(), answer=AsyncMock())
    cb = SimpleNamespace(from_user=SimpleNamespace(id=111), message=message, answer=AsyncMock())
    container = SimpleNamespace(settings=SimpleNamespace(ADMINS=[111]))
    await payments.on_refund_decision(cb, AdmRefund(rid=1, a="ok"), container)
    shown = message.edit_text.await_args.args[0]
    assert expected in shown
    assert "Возврат оформлен" not in shown
    assert "отключен" not in shown
