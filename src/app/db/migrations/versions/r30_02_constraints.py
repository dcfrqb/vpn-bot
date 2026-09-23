"""Release 3.0: constraints (NOT VALID -> VALIDATE), provider widened

Revision ID: r30_02_constraints
Revises: r30_01_additive
Create Date: 2026-09-23

Prod verified read-only 2026-09-23 (BEGIN READ ONLY on crs_vpn_db,
<APP_DB_HOST>): 0 violators for every check below, dup (telegram_user_id,
sub_kind) = 0, max(length(payments.provider)) = 8. See stream_F.md for the
exact query log.

Every CHECK/FK is added NOT VALID first (no table scan, no lock beyond
metadata), then VALIDATE CONSTRAINT in a separate statement (scans without
blocking writes on PG >= 11, which crs_vpn_db is: 14.23). Safe to run while
2.1.1/3.0 code is live: no column is narrowed, no row is rewritten except
the provider type widening below (VARCHAR(16) -> VARCHAR(32), a metadata-only
change in Postgres, no rewrite).

- payments.provider: VARCHAR(16) -> VARCHAR(32) (07_database.md D3; needed
  for provider='telegram_stars' once stream A ships Stars).
- CHECK subscriptions.sub_kind IN ('main','obhod')
- CHECK subscriptions.provisioning_state IN ('pending','synced','failed','expired')
  ('expired' undocumented in 2.1.1 comments but in live use, see 07 D8/Q4)
- CHECK subscriptions.active = false OR valid_until IS NOT NULL OR is_lifetime
- CHECK payments.status IN ('pending','waiting_for_capture','succeeded',
  'canceled','failed','refunded'). Amended before its first prod run
  (integration 3.0, requests/A.md A-1): 2.1 refunds.py writes 'refunded' on
  every full refund, 3.0 on Stars/24h refunds; 'waiting_for_capture' is in
  the 2.x FSM. tests/data/test_constraints_vs_code.py keeps this list equal
  to every status the code writes.
- CHECK payments.amount >= 0
- CHECK payments.status <> 'succeeded' OR paid_at IS NOT NULL
- UNIQUE (telegram_user_id, sub_kind) on subscriptions, full (not partial
  like uq_active_subscription_per_user_kind): the code already treats the
  main row as a singleton reused across renewals (07 Q6); 0 violators today.
- FK subscriptions.autorenew_method_id -> payment_methods.id (promised by
  r30_01's docstring: "FK comes in r30_02"), ON DELETE SET NULL. All-NULL
  today so it validates instantly.

downgrade drops exactly what this revision added.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "r30_02_constraints"
down_revision: Union[str, Sequence[str], None] = "r30_01_additive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CHECKS = [
    ("subscriptions", "ck_subscriptions_sub_kind", "sub_kind IN ('main', 'obhod')"),
    (
        "subscriptions",
        "ck_subscriptions_provisioning_state",
        "provisioning_state IN ('pending', 'synced', 'failed', 'expired')",
    ),
    (
        "subscriptions",
        "ck_subscriptions_active_has_end",
        "active = false OR valid_until IS NOT NULL OR is_lifetime",
    ),
    (
        "payments",
        "ck_payments_status",
        "status IN ('pending', 'waiting_for_capture', 'succeeded', 'canceled', 'failed', 'refunded')",
    ),
    ("payments", "ck_payments_amount_nonneg", "amount >= 0"),
    (
        "payments",
        "ck_payments_succeeded_has_paid_at",
        "status <> 'succeeded' OR paid_at IS NOT NULL",
    ),
]


def upgrade() -> None:
    # 1. widen provider (metadata-only, no rewrite: 16 -> 32 is a widening of
    #    a variable-length type)
    op.execute("ALTER TABLE payments ALTER COLUMN provider TYPE VARCHAR(32)")

    # 2. CHECK constraints: add NOT VALID, validate in a second statement
    for table, name, expr in CHECKS:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr}) NOT VALID")
    for table, name, _ in CHECKS:
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")

    # 3. UNIQUE (telegram_user_id, sub_kind), full — verified 0 violators
    op.execute(
        "ALTER TABLE subscriptions ADD CONSTRAINT uq_subscriptions_user_kind "
        "UNIQUE (telegram_user_id, sub_kind)"
    )

    # 4. FK promised by r30_01 for autorenew_method_id
    op.execute(
        "ALTER TABLE subscriptions ADD CONSTRAINT fk_subscriptions_autorenew_method_id "
        "FOREIGN KEY (autorenew_method_id) REFERENCES payment_methods(id) "
        "ON DELETE SET NULL NOT VALID"
    )
    op.execute(
        "ALTER TABLE subscriptions VALIDATE CONSTRAINT fk_subscriptions_autorenew_method_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS fk_subscriptions_autorenew_method_id"
    )
    op.execute("ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS uq_subscriptions_user_kind")
    for table, name, _ in CHECKS:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.execute("ALTER TABLE payments ALTER COLUMN provider TYPE VARCHAR(16)")
