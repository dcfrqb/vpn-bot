"""
Юнит-тесты для get_or_create_telegram_user — функция должна upsert-ить
строку в Postgres `telegram_users`, иначе FK-зависимые insert-ы в payments
падают с ForeignKeyViolationError.
"""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_session_factory():
    """
    Создаёт mock SessionLocal: callable, возвращающий async context manager,
    yield-ящий AsyncMock session. Все вызовы session.execute/commit логируются.
    """
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)

    session_factory = MagicMock(return_value=cm)
    return session_factory, session


@pytest.mark.asyncio
async def test_creates_new_user_with_all_fields(mock_session_factory):
    """Первый вызов: должен выполнить INSERT с переданными полями."""
    session_factory, session = mock_session_factory

    with patch("app.services.users.ensure_user_in_remnawave", new=AsyncMock(return_value="remna-uuid-123")), \
         patch("app.db.session.SessionLocal", session_factory):
        from app.services.users import get_or_create_telegram_user

        result = await get_or_create_telegram_user(
            telegram_id=111,
            username="new_user",
            first_name="First",
            last_name="Last",
            language_code="ru",
        )

    assert result.telegram_id == 111
    assert result.username == "new_user"
    assert result.remna_user_id == "remna-uuid-123"

    # Должен быть один вызов execute (upsert) и один commit
    assert session.execute.await_count == 1
    assert session.commit.await_count == 1

    # Проверяем что переданный values содержит все non-null поля.
    # compile() с literal_binds=True даёт нам видимый SQL.
    stmt = session.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "111" in compiled
    assert "new_user" in compiled
    assert "First" in compiled
    assert "Last" in compiled
    assert "ru" in compiled
    assert "ON CONFLICT" in compiled.upper()


@pytest.mark.asyncio
async def test_idempotent_upsert_on_conflict_update(mock_session_factory):
    """
    Повторный вызов с не-None полями должен делать DO UPDATE,
    а не DO NOTHING — чтобы username/first_name синхронизировались с Telegram.
    """
    session_factory, session = mock_session_factory

    with patch("app.services.users.ensure_user_in_remnawave", new=AsyncMock(return_value="remna-uuid-X")), \
         patch("app.db.session.SessionLocal", session_factory):
        from app.services.users import get_or_create_telegram_user

        await get_or_create_telegram_user(
            telegram_id=222,
            username="changed_username",
            first_name="NewName",
        )

    stmt = session.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "DO UPDATE" in compiled.upper()
    assert "changed_username" in compiled
    assert "NewName" in compiled


@pytest.mark.asyncio
async def test_none_username_does_not_overwrite_existing(mock_session_factory):
    """
    Если username/first_name=None — они НЕ должны попадать в values/set_,
    иначе при повторном вызове из webhook (yookassa.py:324-330 передаёт всё None)
    мы затёрли бы существующие имена.
    """
    session_factory, session = mock_session_factory

    with patch("app.services.users.ensure_user_in_remnawave", new=AsyncMock(return_value=None)), \
         patch("app.db.session.SessionLocal", session_factory):
        from app.services.users import get_or_create_telegram_user

        await get_or_create_telegram_user(
            telegram_id=333,
            username=None,
            first_name=None,
            last_name=None,
            language_code=None,
        )

    stmt = session.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    # Только telegram_id в values. Username должен отсутствовать в set_.
    assert "333" in compiled
    # При отсутствии non-null полей должен быть DO NOTHING, не DO UPDATE.
    assert "DO NOTHING" in compiled.upper()
    assert "DO UPDATE" not in compiled.upper()


@pytest.mark.asyncio
async def test_partial_fields_only_specified_in_set(mock_session_factory):
    """
    Передан только first_name → в DO UPDATE set_ должен быть ТОЛЬКО first_name.
    Это критично: при повторном вызове из cmd_start с username=None мы не должны
    затереть username, который уже мог быть сохранён из предыдущего вызова.
    """
    session_factory, session = mock_session_factory

    with patch("app.services.users.ensure_user_in_remnawave", new=AsyncMock(return_value=None)), \
         patch("app.db.session.SessionLocal", session_factory):
        from app.services.users import get_or_create_telegram_user

        await get_or_create_telegram_user(
            telegram_id=444,
            username=None,
            first_name="OnlyFirst",
            last_name=None,
        )

    stmt = session.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "OnlyFirst" in compiled
    assert "DO UPDATE" in compiled.upper()
    # username не должно быть в set_ конструкции (только в values оно отсутствует целиком).
    # Грубая проверка: в SET-секции не должно быть "username".
    # SET может содержать что-то типа "first_name = ..." — проверяем что username отсутствует.
    set_section = compiled.upper().split("DO UPDATE SET")[-1]
    assert "USERNAME" not in set_section


@pytest.mark.asyncio
async def test_fallback_when_session_local_none():
    """
    Если БД не настроена (SessionLocal is None) — функция не должна падать,
    а просто вернуть синтетический объект.
    """
    with patch("app.services.users.ensure_user_in_remnawave", new=AsyncMock(return_value="remna-uuid-fallback")), \
         patch("app.db.session.SessionLocal", None):
        from app.services.users import get_or_create_telegram_user

        result = await get_or_create_telegram_user(
            telegram_id=555,
            username="user",
        )

    assert result.telegram_id == 555
    assert result.username == "user"
    assert result.remna_user_id == "remna-uuid-fallback"


@pytest.mark.asyncio
async def test_db_error_does_not_crash():
    """
    Если БД временно отвалилась посреди upsert — функция логирует warning,
    но не пробрасывает исключение наверх.
    """
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("simulated db crash"))
    session.commit = AsyncMock()

    def session_factory():
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=session)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    with patch("app.services.users.ensure_user_in_remnawave", new=AsyncMock(return_value="remna-uuid")), \
         patch("app.db.session.SessionLocal", session_factory):
        from app.services.users import get_or_create_telegram_user

        result = await get_or_create_telegram_user(telegram_id=666, username="x")

    # Не упало — вернулся синтетический объект
    assert result.telegram_id == 666
