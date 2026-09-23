"""Хотфикс 2.1, п.12: живые индексы и таблицы стоп-листа объявлены в моделях."""
from app.db.models import Base, BlockedCard, BlockedUser, Payment, Subscription


def _index(table, name):
    return next((i for i in table.indexes if i.name == name), None)


def test_partial_unique_index_declared():
    idx = _index(Subscription.__table__, "uq_active_subscription_per_user_kind")
    assert idx is not None and idx.unique
    assert [c.name for c in idx.columns] == ["telegram_user_id", "sub_kind"]
    assert "active = true" in str(idx.dialect_options["postgresql"]["where"])


def test_live_indexes_declared():
    assert _index(Subscription.__table__, "ix_subscriptions_provisioning_state_valid_until") is not None
    assert _index(Subscription.__table__, "ix_subscriptions_updated_at") is not None
    assert _index(Payment.__table__, "ix_payments_updated_at") is not None
    assert _index(Payment.__table__, "ix_payments_external_id") is not None


def test_blocklist_models_match_live_ddl():
    bu = BlockedUser.__table__
    bc = BlockedCard.__table__
    assert [c.name for c in bu.primary_key.columns] == ["telegram_id"]
    assert [c.name for c in bc.primary_key.columns] == ["fingerprint"]
    assert not bu.c.blocked_at.nullable and bu.c.blocked_at.server_default is not None
    assert "blocked_users" in Base.metadata.tables and "blocked_cards" in Base.metadata.tables


def test_migration_chain_head():
    import pathlib
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = pathlib.Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "src/app/db/migrations"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1  # 3.0: голова двигается (r30_*), но всегда одна
    assert "7b1c2d3e4f50" in {r.revision for r in script.walk_revisions("base", heads[0])}
    assert script.get_revision("7b1c2d3e4f50").down_revision == "3f6f6890f801"
