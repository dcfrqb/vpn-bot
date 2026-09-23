"""Panel accounts are created only by ProvisioningService (stream B).

/start (get_or_create_telegram_user) only looks up; the FK
invariant (telegram_users row first) is kept; a found account is linked."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services import remna_service
from tests.fakes.remnawave import FakeRemna


async def test_ensure_user_lookup_only_does_not_create():
    fake = FakeRemna()
    with patch.object(remna_service, "RemnaClient", return_value=fake), \
            patch.object(remna_service, "persist_remna_link", AsyncMock()) as link:
        assert await remna_service.ensure_user_in_remnawave(7, create=False) is None
        assert not fake.created and not link.await_count
        fake.add_user(501, "u", telegram_id=7)
        assert await remna_service.ensure_user_in_remnawave(7, create=False) == "501"
        link.assert_awaited()


async def test_get_or_create_telegram_user_looks_up_only():
    from app.services import users

    with patch.object(users, "ensure_user_in_remnawave", AsyncMock(return_value=None)) as ensure, \
            patch("app.db.session.SessionLocal", None):
        u = await users.get_or_create_telegram_user(7, username="ivan")
    assert ensure.await_args.kwargs["create"] is False and u.remna_user_id is None
