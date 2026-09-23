"""Every value the code writes into a CHECK-constrained column is allowed by
r30_02 (requests/A.md A-1: 'refunded' was missing and every full refund would
have failed with an IntegrityError once the constraint was validated).

Values are collected from the source by AST (string literals only; the code
writes these columns with literals or with the enums checked below), so a new
status/state written anywhere fails this test until r30_02 (or a later
revision) and db/models.py allow it.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "app"
MIGRATION = SRC / "db" / "migrations" / "versions" / "r30_02_constraints.py"


def _migration_checks() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location("r30_02_constraints", MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {name: expr for _table, name, expr in mod.CHECKS}


def _allowed(expr: str, column: str) -> set[str]:
    m = re.search(rf"{column} IN \(([^)]*)\)", expr)
    assert m, f"no IN list for {column} in {expr!r}"
    return set(re.findall(r"'([^']*)'", m.group(1)))


def _py_files():
    for p in SRC.rglob("*.py"):
        if "migrations" in p.parts:
            continue
        yield p, ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def _lit(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")


PAYMENT_WRITERS = {"Payment", "create", "add_payment"}
PAYMENT_MODULE_HINTS = ("payments", "checkout", "autopay", "fulfillment", "promo_repo", "money", "refund")


def _payment_statuses() -> dict[str, set[str]]:
    """{status: {file:line}} for every literal the code writes to payments.status."""
    out: dict[str, set[str]] = {}

    def add(v, p, node):
        if v is not None:
            out.setdefault(v, set()).add(f"{p.relative_to(SRC)}:{node.lineno}")

    for p, tree in _py_files():
        payment_module = any(h in p.name for h in PAYMENT_MODULE_HINTS) or "payments" in p.parts
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name == "set_status" and len(node.args) >= 3:
                    add(_lit(node.args[2]), p, node)
                if name in PAYMENT_WRITERS and payment_module:
                    for kw in node.keywords:
                        if kw.arg == "status":
                            add(_lit(kw.value), p, node)
            elif isinstance(node, ast.Assign) and payment_module:
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr == "status" and isinstance(t.value, ast.Name) \
                            and t.value.id in ("payment", "row", "p", "rec"):
                        add(_lit(node.value), p, node)
    return out


def _literals_for(field: str) -> dict[str, set[str]]:
    """Literals assigned to ``field`` anywhere: keyword ``field=...`` and ``x.field = ...``."""
    out: dict[str, set[str]] = {}
    for p, tree in _py_files():
        for node in ast.walk(tree):
            vals = []
            if isinstance(node, ast.keyword) and node.arg == field:
                vals.append((_lit(node.value), node.value))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr == field:
                        vals.append((_lit(node.value), node))
            for v, n in vals:
                if v is not None:
                    out.setdefault(v, set()).add(f"{p.relative_to(SRC)}:{getattr(n, 'lineno', 0)}")
    return out


def test_models_declare_the_same_checks_as_the_migration():
    from app.db.models import Payment, Subscription

    declared = {}
    for table in (Payment.__table__, Subscription.__table__):
        for c in table.constraints:
            if c.__class__.__name__ == "CheckConstraint":
                declared[c.name] = str(c.sqltext)
    assert declared == _migration_checks()


def test_every_payment_status_written_by_code_is_allowed():
    allowed = _allowed(_migration_checks()["ck_payments_status"], "status")
    written = _payment_statuses()
    assert {"pending", "succeeded", "canceled", "refunded"} <= set(written), written  # the scan works
    bad = {v: sorted(where) for v, where in written.items() if v not in allowed}
    assert not bad, f"payments.status values not allowed by ck_payments_status: {bad}"


def test_payment_status_enums_are_allowed():
    from app.domain.models import PaymentStatus
    from app.services.payments.store import PENDING_STATUSES

    allowed = _allowed(_migration_checks()["ck_payments_status"], "status")
    assert {s.value for s in PaymentStatus} <= allowed
    assert set(PENDING_STATUSES) <= allowed


def test_every_provisioning_state_written_by_code_is_allowed():
    allowed = _allowed(_migration_checks()["ck_subscriptions_provisioning_state"], "provisioning_state")
    written = _literals_for("provisioning_state")
    assert {"synced", "failed", "expired"} <= set(written), written
    bad = {v: sorted(w) for v, w in written.items() if v not in allowed}
    assert not bad, f"provisioning_state values not allowed: {bad}"


def test_every_sub_kind_is_allowed():
    from app.domain.models import SubKind

    allowed = _allowed(_migration_checks()["ck_subscriptions_sub_kind"], "sub_kind")
    assert {k.value for k in SubKind} <= allowed
    bad = {v: sorted(w) for v, w in _literals_for("sub_kind").items() if v not in allowed}
    assert not bad, f"sub_kind values not allowed: {bad}"


@pytest.mark.parametrize("field,limit", [("provider", 32)])
def test_varchar_literals_fit(field, limit):
    too_long = {v: sorted(w) for v, w in _literals_for(field).items() if len(v) > limit}
    assert not too_long


def test_payment_kind_and_method_enums_fit_their_columns():
    from app.db.models import Payment
    from app.domain.models import PaymentKind, PaymentMethod

    kind_len = Payment.__table__.c.kind.type.length
    method_len = Payment.__table__.c.method.type.length
    assert all(len(k.value) <= kind_len for k in PaymentKind)
    assert all(len(m.value) <= method_len for m in PaymentMethod)
    for v in _literals_for("kind"):
        assert len(v) <= kind_len, v
    for v in _literals_for("method"):
        assert len(v) <= method_len, v
