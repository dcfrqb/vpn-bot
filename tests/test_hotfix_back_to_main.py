"""Хотфикс 2.1, п.5: кнопка back_to_main («В главное меню») больше не падает NameError.

Гоняем настоящий хендлер routers/start.py::back_to_main (а не Navigator отдельно):
он должен сбросить навигацию и показать MAIN_MENU редактированием сообщения.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from app.ui.screens import ScreenID


def _callback(user_id: int = 777) -> MagicMock:
    user = types.User(id=user_id, is_bot=False, first_name="Ann", last_name=None, username="ann")
    cb = MagicMock(spec=types.CallbackQuery)
    cb.from_user = user
    cb.data = "back_to_main"
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_back_to_main_shows_main_menu_without_name_error():
    from app.routers import start as start_router
    from app.navigation.navigator import get_navigator

    cb = _callback()
    navigator = get_navigator()
    navigator._set_current_screen(cb.from_user.id, ScreenID.SUBSCRIPTION_PLAN_DETAIL)

    fake_sm = MagicMock()
    fake_sm._backstacks = {cb.from_user.id: [ScreenID.MAIN_MENU, ScreenID.SUBSCRIPTION_PLANS]}
    fake_sm._set_current_screen = MagicMock()
    fake_sm.show_screen = AsyncMock(return_value=True)
    vm = object()

    with patch("app.ui.screen_manager.get_screen_manager", return_value=fake_sm), \
         patch.object(start_router, "get_main_menu_viewmodel", AsyncMock(return_value=vm)):
        await start_router.back_to_main(cb)

    cb.answer.assert_awaited()
    fake_sm.show_screen.assert_awaited_once()
    kwargs = fake_sm.show_screen.await_args.kwargs
    assert kwargs["screen_id"] == ScreenID.MAIN_MENU
    assert kwargs["edit"] is True
    assert kwargs["viewmodel"] is vm
    assert navigator.get_current_screen(cb.from_user.id) == ScreenID.MAIN_MENU
    fake_sm.reset_to.assert_called_once_with(cb.from_user.id, ScreenID.MAIN_MENU)


def test_no_undefined_names_in_src():
    """pyflakes-проверка: в src/app нет undefined name (как было с get_navigator)."""
    import pathlib
    try:
        from pyflakes import api as pf_api
        from pyflakes import reporter as pf_reporter
    except ImportError:  # pragma: no cover
        pytest.skip("pyflakes не установлен")
    import io

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "app"
    out, err = io.StringIO(), io.StringIO()
    rep = pf_reporter.Reporter(out, err)
    for path in root.rglob("*.py"):
        pf_api.checkPath(str(path), rep)
    undefined = [line for line in out.getvalue().splitlines() if "undefined name" in line]
    assert undefined == []
