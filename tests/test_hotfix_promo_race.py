"""Хотфикс 2.1, п.4: промо под локом + record-first, выключатели промо, /trial = 5 дней."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from app.routers import start as start_router
from app.services.sync_service import SyncResult
from tests.fakes.redis import FakeRedis


def _message(user_id=4242, text="/trial"):
    msg = MagicMock(spec=types.Message)
    msg.from_user = types.User(id=user_id, is_bot=False, first_name="Petya", username=None, language_code="ru")
    msg.text = text
    msg.answer = AsyncMock()
    msg.bot = AsyncMock()
    return msg


class _NoRowSession:
    """SessionLocal() для проверки «уже использовал?» и поиска remna id: строк нет."""

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        return r


class FakePromoLedger:
    """Уникальный external_id как в payments (record-first)."""

    def __init__(self):
        self.rows = set()
        self.deleted = []

    async def record(self, user_id, external_id, description, metadata):
        await asyncio.sleep(0)
        if external_id in self.rows:
            return "used"
        self.rows.add(external_id)
        return 1

    async def delete(self, external_id):
        self.rows.discard(external_id)
        self.deleted.append(external_id)


def _patches(ledger, provision, redis):
    sync = MagicMock()
    sync.sync_user_and_subscription = AsyncMock(
        return_value=SyncResult(False, None, "none", None, "remna")
    )
    return [
        patch.object(start_router, "get_or_create_telegram_user", AsyncMock()),
        patch.object(start_router, "SyncService", return_value=sync),
        patch.object(start_router, "_record_promo_usage", ledger.record),
        patch.object(start_router, "_delete_promo_usage", ledger.delete),
        patch("app.services.remna_service.provision_tariff", provision),
        patch("app.db.session.SessionLocal", _NoRowSession()),
        patch("app.services.cache.get_redis_client", return_value=redis),
        patch("app.services.jsonl_logger.log_payment_event", MagicMock()),
    ]


async def _run_parallel(n, redis):
    ledger = FakePromoLedger()

    async def slow_provision(*a, **kw):
        await asyncio.sleep(0.01)
        return True

    provision = AsyncMock(side_effect=slow_provision)
    msgs = [_message() for _ in range(n)]
    ps = _patches(ledger, provision, redis)
    for p in ps:
        p.start()
    try:
        await asyncio.gather(*(start_router.cmd_trial(m) for m in msgs))
    finally:
        for p in ps:
            p.stop()
    return provision, ledger, msgs


@pytest.mark.asyncio
async def test_parallel_trial_grants_once_with_redis_lock():
    provision, ledger, _ = await _run_parallel(5, FakeRedis())
    assert provision.await_count == 1
    assert ledger.rows == {"promo_trial_4242"}


@pytest.mark.asyncio
async def test_parallel_trial_grants_once_even_without_redis():
    """Redis лежит: лок fail-open, но record-first не дает второй выдачи."""
    provision, ledger, msgs = await _run_parallel(5, None)
    assert provision.await_count == 1
    texts = [m.answer.await_args.args[0] for m in msgs]
    assert sum("уже был использован" in t for t in texts) == 4


@pytest.mark.asyncio
async def test_trial_is_5_days_standard():
    from app.services.remna_service import TARIFF_TO_DAYS

    assert TARIFF_TO_DAYS["trial_standard_5d"] == ("standard", 5)
    assert TARIFF_TO_DAYS["trial_standard_10d"][1] == 5, "старый ключ тоже не дает 10 дней"
    provision, _, msgs = await _run_parallel(1, FakeRedis())
    assert provision.await_args.args[1] == "trial_standard_5d"
    assert "на 5 дней" in msgs[0].answer.await_args.args[0]


@pytest.mark.asyncio
async def test_provision_failure_rolls_back_usage_record():
    ledger = FakePromoLedger()
    provision = AsyncMock(return_value=False)
    msg = _message()
    ps = _patches(ledger, provision, FakeRedis())
    for p in ps:
        p.start()
    try:
        await start_router.cmd_trial(msg)
    finally:
        for p in ps:
            p.stop()
    assert ledger.rows == set()
    assert ledger.deleted == ["promo_trial_4242"]


@pytest.mark.asyncio
async def test_db_error_on_record_means_no_grant():
    provision = AsyncMock(return_value=True)
    ledger = FakePromoLedger()

    async def broken_record(*a, **kw):
        return None

    ledger.record = broken_record
    msg = _message()
    ps = _patches(ledger, provision, FakeRedis())
    for p in ps:
        p.start()
    try:
        await start_router.cmd_trial(msg)
    finally:
        for p in ps:
            p.stop()
    provision.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_flags_really_disable(monkeypatch):
    from app.config import settings

    provision = AsyncMock(return_value=True)
    with patch("app.services.remna_service.provision_tariff", provision):
        monkeypatch.setattr(settings, "PROMO_TRIAL_ENABLED", False)
        monkeypatch.setattr(settings, "PROMO_SOLOKHIN_ENABLED", False)
        monkeypatch.setattr(settings, "PROMO_SUN718_ENABLED", False)
        for handler in (start_router.cmd_trial, start_router.cmd_solokhin, start_router.cmd_sun718):
            msg = _message()
            await handler(msg)
            msg.answer.assert_not_awaited()
    provision.assert_not_awaited()


def test_promo_flags_are_settings_fields(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("PROMO_SOLOKHIN_ENABLED", "false")
    monkeypatch.setenv("PROMO_TRIAL_ENABLED", "false")
    monkeypatch.setenv("PROMO_ADMIN_ENABLED", "false")
    s = Settings(_env_file=None)
    assert s.PROMO_SOLOKHIN_ENABLED is False
    assert s.PROMO_TRIAL_ENABLED is False
    assert s.PROMO_ADMIN_ENABLED is False


def test_admin_ids_fallback_no_attribute_error(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("ADMINS", raising=False)
    s = Settings(_env_file=None, admin_ids="11, 22")
    assert s.ADMINS == [11, 22]


def test_unknown_env_keys_do_not_crash(tmp_path):
    from app.config import Settings

    env = tmp_path / ".env"
    env.write_text("POSTGRES_DB=x\nCRYPTO_NETWORK=TRC20\nSOMETHING_ELSE=1\n")
    s = Settings(_env_file=str(env))
    assert s.CRYPTO_NETWORK == "TRC20"


@pytest.mark.asyncio
async def test_parallel_sun718_grants_once():
    ledger = FakePromoLedger()

    async def slow_provision(*a, **kw):
        await asyncio.sleep(0.01)
        return True

    provision = AsyncMock(side_effect=slow_provision)
    msgs = [_message(text="/sun718") for _ in range(4)]
    ps = _patches(ledger, provision, None) + [
        patch.object(start_router, "_sun718_notify_admins", AsyncMock()),
    ]
    for p in ps:
        p.start()
    try:
        await asyncio.gather(*(start_router.cmd_sun718(m) for m in msgs))
    finally:
        for p in ps:
            p.stop()
    assert provision.await_count == 1
    assert ledger.rows == {"promo_sun718_4242"}


@pytest.mark.asyncio
async def test_friend_grant_locked_per_target_user():
    """Две разные кнопки выдачи одному юзеру одновременно: выдача одна."""
    from app.routers import admin as admin_router

    async def slow_provision(*a, **kw):
        await asyncio.sleep(0.02)
        return True

    provision = AsyncMock(side_effect=slow_provision)

    def _cb(data):
        cb = MagicMock(spec=types.CallbackQuery)
        cb.from_user = types.User(id=1, is_bot=False, first_name="Admin")
        cb.data = data
        cb.answer = AsyncMock()
        cb.message = MagicMock()
        cb.message.text = "Запрос на доступ"
        cb.message.edit_text = AsyncMock()
        cb.bot = AsyncMock()
        return cb

    redis = FakeRedis()
    with patch("app.services.remna_service.provision_tariff", provision), \
         patch("app.services.cache.get_redis_client", return_value=redis):
        await asyncio.gather(
            admin_router._handle_friend_grant(_cb("friend_grant_1m_777"), "1m"),
            admin_router._handle_admin_promo_grant(_cb("admin_promo_grant_3m_777"), "3m"),
        )
    assert provision.await_count == 1
