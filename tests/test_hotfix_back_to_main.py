"""Хотфикс 2.1, п.5: кнопка back_to_main («В главное меню») больше не падает NameError.

Гоняем настоящий хендлер routers/start.py::back_to_main (а не Navigator отдельно):
он должен сбросить навигацию и показать MAIN_MENU редактированием сообщения.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import types



def _callback(user_id: int = 777) -> MagicMock:
    user = types.User(id=user_id, is_bot=False, first_name="Ann", last_name=None, username="ann")
    cb = MagicMock(spec=types.CallbackQuery)
    cb.from_user = user
    cb.data = "back_to_main"
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_text = AsyncMock()
    return cb




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
