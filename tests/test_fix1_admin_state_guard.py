"""Фикс-раунд 1: 05 S-7 (роль для STATE-действий админских экранов) и
F3 (публичный reset_to у Navigator/ScreenManager)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from app.ui.screens import ScreenID

NON_ADMIN_ID = 900000011


def _callback(user_id: int) -> MagicMock:
    cb = MagicMock(spec=types.CallbackQuery)
    cb.from_user = types.User(id=user_id, is_bot=False, first_name="Test")
    cb.data = "ui:admin_users:page:1"
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    return cb


@pytest.mark.asyncio
@pytest.mark.parametrize("screen_id,action,payload", [
    (ScreenID.ADMIN_USERS, "page", "1"),
    (ScreenID.ADMIN_PAYMENTS, "page", "1"),
    (ScreenID.ADMIN_PAYMENTS, "filter", "succeeded"),
    (ScreenID.ADMIN_STATS, "refresh", "-"),
])
async def test_non_admin_gets_nothing_from_admin_state_actions(screen_id, action, payload):
    from app.ui.screen_manager import get_screen_manager

    cb = _callback(NON_ADMIN_ID)
    users = AsyncMock(return_value=([], 0))
    payments = AsyncMock(return_value=([], 0))
    stats = AsyncMock(return_value={})
    sm = get_screen_manager()
    with patch("app.ui.screen_manager.is_admin", return_value=False), \
         patch("app.services.stats.get_users_list", users), \
         patch("app.services.stats.get_payments_list", payments), \
         patch("app.services.stats.get_statistics", stats), \
         patch.object(sm, "show_screen", AsyncMock(return_value=True)) as show:
        ok = await sm.handle_action(screen_id, action, payload, cb, user_id=NON_ADMIN_ID)

    assert ok is False
    users.assert_not_called()
    payments.assert_not_called()
    stats.assert_not_called()
    show.assert_not_called()
    cb.message.edit_text.assert_not_called()


@pytest.mark.asyncio
async def test_admin_screens_refuse_non_admin_directly():
    """Вторая линия: сами экраны тоже проверяют роль."""
    from app.ui.screens.admin import AdminPaymentsScreen, AdminStatsScreen, AdminUsersScreen

    cb = _callback(NON_ADMIN_ID)
    with patch("app.ui.screens.admin.is_admin", return_value=False), \
         patch("app.services.stats.get_users_list", AsyncMock()) as users, \
         patch("app.services.stats.get_payments_list", AsyncMock()) as payments:
        assert await AdminUsersScreen().handle_action("page", "1", cb, NON_ADMIN_ID) is False
        assert await AdminPaymentsScreen().handle_action("filter", "all", cb, NON_ADMIN_ID) is False
        assert await AdminStatsScreen().handle_action("refresh", "-", cb, NON_ADMIN_ID) is False
    users.assert_not_called()
    payments.assert_not_called()


def test_reset_to_syncs_navigator_and_screen_manager():
    from app.navigation.navigator import get_navigator
    from app.ui.screen_manager import get_screen_manager

    uid = 900000012
    nav = get_navigator()
    sm = get_screen_manager()
    sm._backstacks[uid] = [ScreenID.MAIN_MENU, ScreenID.SUBSCRIPTION_PLANS]
    sm._set_current_screen(uid, ScreenID.SUBSCRIPTION_PLAN_DETAIL)
    nav._set_current_screen(uid, ScreenID.SUBSCRIPTION_PLAN_DETAIL)
    nav.set_flow_anchor(uid, ScreenID.SUBSCRIPTION_PLANS)

    sm.reset_to(uid, ScreenID.MAIN_MENU)

    assert uid not in sm._backstacks
    assert sm._get_current_screen(uid) == ScreenID.MAIN_MENU
    assert nav.get_current_screen(uid) == ScreenID.MAIN_MENU
    assert nav.get_backstack(uid) == []
    assert uid not in nav._flow_anchors
