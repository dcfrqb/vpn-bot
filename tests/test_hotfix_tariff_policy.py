"""Хотфикс 2.1, п.3: выдача тарифа не затирает ручные сквады и не понижает лимит устройств."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.remna_tariff import (
    RemnaTariffError,
    apply_tariff_to_remna_user,
    merge_tariff_squads,
    resolve_device_limit,
)
from tests.fakes.remnawave import FakeRemna


def test_merge_keeps_foreign_and_swaps_tariff():
    managed = {"sq-basic", "sq-lite", "sq-pro"}
    assert merge_tariff_squads(["sq-pro-friend", "sq-basic"], "sq-lite", managed) == ["sq-pro-friend", "sq-lite"]
    assert merge_tariff_squads([], "sq-lite", managed) == ["sq-lite"]
    assert merge_tariff_squads(["sq-lite"], "sq-lite", managed) == ["sq-lite"]


@pytest.mark.parametrize(
    "current,plan,foreign,expected",
    [
        (20, 2, False, 20),      # поднятый вручную лимит не понижаем
        (2, 10, False, 10),      # апгрейд поднимает
        (0, 5, False, None),     # 0 выставлен вручную, не трогаем
        (None, 5, True, None),   # NULL у ручного юзера (есть чужие сквады) не трогаем
        (None, 5, False, 5),     # NULL у юзера, созданного ботом при /start, ставим лимит тарифа
    ],
)
def test_resolve_device_limit(current, plan, foreign, expected):
    assert resolve_device_limit(current, plan, foreign) == expected


@pytest.mark.asyncio
async def test_friend_user_renewal_keeps_friend_squad_and_limit():
    fake = FakeRemna()
    fake.add_user(2001, "tg_test_friend", telegram_id=1, squads=["pro-friend"], limit=15)
    payload = await apply_tariff_to_remna_user(fake, "2001", "basic", expire_at="2026-12-01T00:00:00Z")
    assert len(fake.patches) == 1, "одним PATCH"
    assert set(fake.squad_names(2001)) == {"pro-friend", "basic"}
    assert fake.users[2001]["hwidDeviceLimit"] == 15
    assert "hwid_device_limit" not in payload
    assert fake.patches[0]["expireAt"] == "2026-12-01T00:00:00Z"


@pytest.mark.asyncio
async def test_plan_change_swaps_only_tariff_squad():
    fake = FakeRemna()
    fake.add_user(5, "tg_x", telegram_id=2, squads=["lite", "arcadia", "us-2"], limit=2)
    await apply_tariff_to_remna_user(fake, "5", "pro")
    assert set(fake.squad_names(5)) == {"arcadia", "us-2", "pro"}
    assert fake.users[5]["hwidDeviceLimit"] == 10


@pytest.mark.asyncio
async def test_squad_missing_raises_and_writes_nothing():
    fake = FakeRemna(squads={"basic": "sq-basic"})
    fake.add_user(7, "tg_y", telegram_id=3, squads=[], limit=None)
    with pytest.raises(RemnaTariffError):
        await apply_tariff_to_remna_user(fake, "7", "pro", expire_at="2026-12-01T00:00:00Z")
    assert fake.patches == []


@pytest.mark.asyncio
async def test_panel_down_raises():
    fake = FakeRemna()
    fake.add_user(8, "tg_z", telegram_id=4)
    fake.fail_get_user = True
    with pytest.raises(RemnaTariffError):
        await apply_tariff_to_remna_user(fake, "8", "lite")


@pytest.mark.asyncio
async def test_provision_tariff_uses_policy():
    """Промо/админ-выдача (provision_tariff) тоже не сносит ручные сквады."""
    from app.services import remna_service

    fake = FakeRemna()
    fake.add_user(42, "tg_friend", telegram_id=4242, squads=["premium-friend"], limit=15,
                  expire="2000-01-01T00:00:00Z")
    with patch.object(remna_service, "RemnaClient", return_value=fake), \
         patch.object(remna_service, "ensure_user_in_remnawave", AsyncMock(return_value="42")), \
         patch("app.db.session.SessionLocal", None):
        ok = await remna_service.provision_tariff(4242, "solokhin_15d", req_id="t")
    assert ok is True
    assert set(fake.squad_names(42)) == {"premium-friend", "premium"}
    assert fake.users[42]["hwidDeviceLimit"] == 15
    assert len(fake.patches) == 1


@pytest.mark.asyncio
async def test_sun718_revert_keeps_manual_squads_and_limit():
    from app.tasks import sun718_revert as rv

    fake = FakeRemna()
    fake.add_user(2002, "tg_test_user", telegram_id=900000002, squads=["pro", "arcadia"], limit=12)

    payment = SimpleNamespace(
        id=1, telegram_user_id=900000002,
        payment_metadata={"pre_promo_plan": "lite", "revert_completed": False},
    )
    tg_user = SimpleNamespace(remna_user_id="2002")

    class _Res:
        def scalar_one_or_none(self):
            return tg_user

    session = MagicMock()
    session.get = AsyncMock(return_value=payment)
    session.execute = AsyncMock(return_value=_Res())
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)

    task = rv.Sun718RevertTask(bot=AsyncMock())
    with patch("app.db.session.SessionLocal", MagicMock(return_value=cm)), \
         patch("app.remnawave.client.RemnaClient", return_value=fake), \
         patch("app.routers.start._get_last_paid_plan_code", AsyncMock(return_value="lite")), \
         patch.object(task, "_mark_completed", AsyncMock()) as mark:
        await task._revert_one(1)
    assert set(fake.squad_names(2002)) == {"arcadia", "lite"}
    assert fake.users[2002]["hwidDeviceLimit"] == 12
    mark.assert_awaited_once()


class _Res:
    def __init__(self, obj):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


# test_payment_renewal_path_preserves_manual_squads: removed in 3.0 with the 2.x provisioning (the payment path is stream B provisioning)


# --------------------------------------------------------------------------
# Решение владельца 23.09.2026: сквады *-m = ручные плательщики, бот их
# никогда не трогает (как *-friend и arcadia)
# --------------------------------------------------------------------------

def test_manual_squad_names_are_never_managed():
    from app.services.remna_tariff import is_manual_squad_name, managed_tariff_squad_names

    for name in ("pro-m", "lite-m", "standard-m", "premium-m", "pro-friend", "arcadia"):
        assert is_manual_squad_name(name), name
        assert name not in managed_tariff_squad_names()
    for name in ("pro", "lite", "basic", "standard", "premium"):
        assert not is_manual_squad_name(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("plan", ["lite", "pro", "standard"])  # даунгрейд, продление, смена
async def test_pro_m_is_kept_on_downgrade_and_renewal(plan):
    fake = FakeRemna()
    fake.add_user(2003, "tg_test_manual", telegram_id=900000004, squads=["pro-m", "pro"], limit=10)
    await apply_tariff_to_remna_user(fake, "2003", plan, expire_at="2027-01-01T00:00:00Z",
                                     enable_if_disabled=True)
    names = set(fake.squad_names(2003))
    assert "pro-m" in names
    assert plan in names
    assert names - {"pro-m", plan} == set()


# test_resync_keeps_manual_m_squad: removed in 3.0 with the 2.x provisioning (the payment path is stream B provisioning)
