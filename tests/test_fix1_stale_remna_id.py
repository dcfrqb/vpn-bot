"""Фикс-раунд 1, ревью money B1: сохраненный remna_user_id удаленного в панели
юзера (или legacy-UUID) больше не блокирует выдачу навсегда. Как на проде:
забываем id, ищем по telegramId, иначе создаем. Ошибка панели — не «юзера нет».
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fakes.remnawave import FakeRemna

TG_ID = 900000003


class _Res:
    def __init__(self, obj=None):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


def _session(tg, sub):
    session = MagicMock()
    # tg, sub, затем любые служебные запросы (upsert remna_users / select remna_users)
    session.execute = AsyncMock(side_effect=[_Res(tg), _Res(sub)] + [_Res(None)] * 5)
    session.commit = AsyncMock()
    session.add = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return session, cm


def _objs(stored_id):
    tg = SimpleNamespace(telegram_id=TG_ID, remna_user_id=stored_id, username="tg_test_user",
                         first_name=None, last_name=None)
    sub = SimpleNamespace(id=31, plan_code="lite", remnawave_expected_expire_at=datetime(2099, 1, 1),
                          valid_until=None, config_data={}, remna_user_id=stored_id)
    return tg, sub


async def _run(fake, tg, sub):
    from app.services.payments import yookassa as yk

    _, cm = _session(tg, sub)
    with patch.object(yk, "SessionLocal", MagicMock(return_value=cm)), \
         patch.object(yk, "RemnaClient", return_value=fake):
        return await yk.get_or_create_remna_user_and_get_subscription_url(
            telegram_user_id=TG_ID, subscription_id=31, period_months=1,
        )


@pytest.mark.asyncio
async def test_deleted_stored_user_falls_back_to_live_user_by_telegram_id():
    fake = FakeRemna()
    fake.add_user(3002, "tg_test_user", telegram_id=TG_ID, squads=[], expire="2026-01-01T00:00:00Z")
    tg, sub = _objs("3001")  # 3001 удален в панели -> 404

    url = await _run(fake, tg, sub)

    assert url == "https://sub.example/3002"
    assert tg.remna_user_id == "3002"
    assert sub.remna_user_id == "3002"
    assert fake.squad_names(3002) == ["lite"]
    assert fake.users[3002]["expireAt"].startswith("2099")
    assert fake.created == []


@pytest.mark.asyncio
async def test_legacy_uuid_stored_id_falls_back_to_create():
    fake = FakeRemna()
    tg, sub = _objs("0b5e6c1e-1111-4222-8333-944455556666")

    url = await _run(fake, tg, sub)

    assert len(fake.created) == 1
    new_id = fake.created[0]["id"]
    assert url == f"https://sub.example/{new_id}"
    assert tg.remna_user_id == str(new_id)
    assert fake.created[0]["telegram_id"] == TG_ID


@pytest.mark.asyncio
async def test_panel_down_is_not_user_gone():
    fake = FakeRemna()
    fake.add_user(3001, "tg_test_user", telegram_id=TG_ID, squads=["lite"])
    fake.fail_get_user = True
    tg, sub = _objs("3001")

    url = await _run(fake, tg, sub)

    assert url is None  # выдача не засчитана, будет retry
    assert tg.remna_user_id == "3001"  # id не забыт
    assert fake.created == []
    assert fake.patches == []
