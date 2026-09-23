"""Хотфикс 2.1, п.6: «Получить ссылку» после оплаты ведет на экран «Подключиться»
(там Pro видит и ссылку обхода); старый callback get_subscription_link — алиас."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from app.ui.screens import ScreenID


def test_link_buttons_use_connect_callback():
    from app.keyboards import get_subscription_info_keyboard, get_subscription_link_keyboard

    for kb in (get_subscription_link_keyboard(), get_subscription_info_keyboard(has_subscription=True)):
        datas = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "connect_vpn" in datas
        assert "get_subscription_link" not in datas


@pytest.mark.asyncio
async def test_old_callback_is_alias_to_connect_flow():
    from app.legacy.routers import payments as legacy_payments

    cb = MagicMock(spec=types.CallbackQuery)
    cb.from_user = types.User(id=31, is_bot=False, first_name="Pro")
    cb.data = "get_subscription_link"
    cb.answer = AsyncMock()
    sm = MagicMock()
    sm.handle_action = AsyncMock(return_value=True)
    with patch("app.ui.screen_manager.get_screen_manager", return_value=sm):
        await legacy_payments.get_subscription_link(cb)
    sm.handle_action.assert_awaited_once()
    kw = sm.handle_action.await_args.kwargs
    assert kw["screen_id"] == ScreenID.CONNECT
    assert kw["action"] == "open"
    assert kw["user_id"] == 31
