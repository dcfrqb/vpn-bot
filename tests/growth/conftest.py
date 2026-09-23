"""Fixtures of stream E tests."""
from __future__ import annotations

import pytest

from tests.fakes.notifier import RecordingNotifier
from tests.fakes.redis import FakeRedis
from tests.growth.fakes import FakeProvisioning, FakeStatus, MemoryPromoRepo, Settings


@pytest.fixture
def redis(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr("app.services.cache.get_redis_client", lambda: r)
    return r


@pytest.fixture
def engine_parts(redis):
    from app.services.promo import PromoEngine

    status = FakeStatus()
    prov = FakeProvisioning(status)
    notifier = RecordingNotifier()
    repo = MemoryPromoRepo()
    settings = Settings()
    engine = PromoEngine(provisioning=prov, status=status, notifier=notifier, repo=repo, settings=settings)
    return engine, repo, prov, status, notifier, settings
