"""Stream F: static checks for r30_02/r30_03/r30_04-draft that don't need a
real Postgres (the real-Postgres round trip is covered in CI's `alembic`
job: upgrade/check/downgrade/upgrade/check on an empty database, see
.github/workflows/ci.yml)."""
import importlib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]


def _script_dir() -> ScriptDirectory:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    return ScriptDirectory.from_config(cfg)


def test_single_alembic_head():
    heads = _script_dir().get_heads()
    assert len(heads) == 1, f"expected exactly one alembic head, got {heads}"


def test_r30_chain_is_linear_and_reaches_head():
    sd = _script_dir()
    revs = ["r30_01_additive", "r30_02_constraints", "r30_03_data"]
    for rev in revs:
        script = sd.get_revision(rev)
        assert script is not None, f"revision {rev} not found"
    # r30_03_data must be (or be an ancestor of) the current head
    head = sd.get_revision(sd.get_heads()[0])
    ancestors = {s.revision for s in sd.iterate_revisions(head.revision, None)}
    assert "r30_03_data" in ancestors


def test_r30_04_drops_is_a_draft_not_a_real_migration():
    """r30_04_drops must stay a .py.draft file (not .py) until the owner
    signs off, per its own docstring: it must not create a second alembic
    head."""
    draft = REPO_ROOT / "src/app/db/migrations/versions/r30_04_drops.py.draft"
    real = REPO_ROOT / "src/app/db/migrations/versions/r30_04_drops.py"
    assert draft.exists(), "r30_04_drops.py.draft should exist (prepared, not applied)"
    assert not real.exists(), "r30_04_drops.py must not exist yet -- would create a second head"


def test_models_declare_the_r30_02_constraints():
    """The constraints r30_02_constraints.py adds on prod must also be
    declared in db/models.py (alembic check must stay clean, and this test
    fails fast in a plain unit run, without needing Postgres)."""
    from app.db import models

    sub_names = {c.name for c in models.Subscription.__table_args__ if hasattr(c, "name")}
    pay_names = {c.name for c in models.Payment.__table_args__ if hasattr(c, "name")}

    for name in (
        "uq_subscriptions_user_kind",
        "ck_subscriptions_sub_kind",
        "ck_subscriptions_provisioning_state",
        "ck_subscriptions_active_has_end",
    ):
        assert name in sub_names, f"{name} missing from Subscription.__table_args__"

    for name in ("ck_payments_status", "ck_payments_amount_nonneg", "ck_payments_succeeded_has_paid_at"):
        assert name in pay_names, f"{name} missing from Payment.__table_args__"

    assert models.Payment.__table__.c.provider.type.length == 32, "payments.provider must be VARCHAR(32) (r30_02)"

    fk_targets = {
        (fk.column.table.name, fk.column.name)
        for fk in models.Subscription.__table__.c.autorenew_method_id.foreign_keys
    }
    assert ("payment_methods", "id") in fk_targets, "autorenew_method_id must FK to payment_methods.id (r30_02)"


def test_r30_03_plan_name_case_matches_domain_plan_catalog():
    """The literal SQL CASE in r30_03_data.py must stay in sync with
    app.domain.plans.PLAN_CATALOG display strings, so the migration's
    hardcoded copy (migrations must not import application code, see the
    migration's own docstring) does not silently drift."""
    migrations_mod = importlib.import_module(
        "app.db.migrations.versions.r30_03_data"
    )
    case_sql = migrations_mod.PLAN_NAME_CASE

    from app.domain.plans import PLAN_CATALOG

    for code in ("basic", "premium", "lite", "standard", "pro"):
        display = PLAN_CATALOG[code]["display"]
        assert f"'{code}' THEN '{display}'" in case_sql, (
            f"r30_03_data.PLAN_NAME_CASE is out of sync with domain.plans for {code!r} "
            f"(expected display {display!r})"
        )
