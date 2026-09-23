"""Хотфикс 2.1, п.13: алерт по неприменному пакету обхода (04 M9), статистика только
по main (04 M10), ротация deep-скана реконсилера (07 §6 п.6)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql


class _Capture:
    """Фейковая сессия: пишет SQL всех execute, отдает заданные результаты."""

    def __init__(self, results):
        self.sql = []
        self.results = list(results)
        self.commit = AsyncMock()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        self.sql.append(str(stmt.compile(dialect=postgresql.dialect())))
        return self.results.pop(0) if self.results else MagicMock()


@pytest.mark.asyncio
async def test_active_subscriptions_count_only_main():
    from app.services import stats

    session = _Capture([MagicMock(scalar=lambda: 1) for _ in range(7)])
    with patch("app.services.stats.SessionLocal", session, create=True), \
         patch("app.db.session.SessionLocal", session):
        await stats.get_statistics()
    active_sql = [q for q in session.sql if "subscriptions" in q and "count" in q.lower()]
    assert active_sql and all("sub_kind" in q for q in active_sql)


@pytest.mark.asyncio
async def test_deep_scan_orders_by_last_check_and_bumps_verified():
    from datetime import datetime, timedelta
    from app.tasks import remnawave_reconciler as rr
    from tests.fakes.remnawave import FakeRemna

    expected = datetime.utcnow() + timedelta(days=10)
    sub = SimpleNamespace(id=5, remna_user_id="9", valid_until=expected, telegram_user_id=1)
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [sub]
    session = _Capture([rows, MagicMock()])

    fake = FakeRemna()
    fake.add_user(9, "tg_x", telegram_id=1, expire=expected.strftime("%Y-%m-%dT%H:%M:%SZ"))
    rec = rr.RemnawaveReconciler(bot=AsyncMock())
    with patch.object(rr, "SessionLocal", session), patch.object(rr, "RemnaClient", return_value=fake):
        out = await rec._deep_scan()
    assert out["deep_scanned"] == 1
    select_sql = session.sql[0]
    assert "ORDER BY subscriptions.remnawave_synced_at ASC NULLS FIRST" in select_sql
    assert any(q.startswith("UPDATE subscriptions SET remnawave_synced_at") for q in session.sql[1:])


@pytest.mark.asyncio
async def test_obhod_package_not_applied_alerts_admin_once():
    from app.services.payments import yookassa as yk
    from tests.test_hotfix_paid_no_squad import EntitySession
    from app.db.models import Payment as PaymentModel

    payment = SimpleNamespace(
        id=3, external_id="ext-3", provider="yookassa", amount=599.0, currency="RUB",
        subscription_id=None, status="succeeded", paid_at=None,
        payment_metadata={"plan_code": "obhod_250", "period_months": 1, "expected_amount": 599},
    )
    session = EntitySession({PaymentModel: payment})
    bot = AsyncMock()
    with patch("app.services.obhod_service.apply_obhod_package", AsyncMock(return_value=False)), \
         patch.object(yk.settings, "ADMINS", [900]):
        for _ in range(2):  # повтор recovery не спамит
            await yk.handle_successful_payment(
                session=session, payment_id=3, telegram_user_id=77, amount=599.0,
                description="x", bot=bot, trace_id="t",
            )
    admin = [c for c in bot.send_message.await_args_list if c.kwargs["chat_id"] == 900]
    user = [c for c in bot.send_message.await_args_list if c.kwargs["chat_id"] == 77]
    assert len(admin) == 1 and len(user) == 1
    assert payment.payment_metadata["obhod_package_applied"] is False
