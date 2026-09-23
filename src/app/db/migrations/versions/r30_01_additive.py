"""Release 3.0: additive schema (new columns NULL, new tables)

Revision ID: r30_01_additive
Revises: 7b1c2d3e4f50
Create Date: 2026-09-23

Strictly additive and idempotent, safe to run on the live 2.1.1 database
while 2.1.1 code is still running:
  - ALTER TABLE ... ADD COLUMN IF NOT EXISTS <col> NULL (no defaults, no
    rewrites, no constraints on existing tables);
  - CREATE TABLE only when the table does not exist, with its own indexes.

payments:      plan_code, period_months, kind, method, card_fingerprint,
               telegram_charge_id, refunded_amount
subscriptions: autorenew, autorenew_method_id (FK comes in r30_02),
               grace_until, grace_state
broadcasts:    segment_params, credit_days  (``segment`` already exists)
new tables:    payment_methods, promo_codes, promo_redemptions,
               refund_requests, trials

Later revisions belong to stream F only: r30_02_* constraints (NOT VALID ->
VALIDATE), r30_03_* data backfills, r30_04_* drops (3.0.1), r30_05+ requests.

downgrade drops only what this revision added (dev/test use; on prod it
would lose 3.0 data).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r30_01_additive"
down_revision: Union[str, Sequence[str], None] = "7b1c2d3e4f50"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_COLUMNS = {
    "payments": [
        ("plan_code", "VARCHAR(32)"),
        ("period_months", "INTEGER"),
        ("kind", "VARCHAR(24)"),
        ("method", "VARCHAR(24)"),
        ("card_fingerprint", "TEXT"),
        ("telegram_charge_id", "VARCHAR(128)"),
        ("refunded_amount", "NUMERIC(10, 2)"),
    ],
    "subscriptions": [
        ("autorenew", "BOOLEAN"),
        ("autorenew_method_id", "INTEGER"),
        ("grace_until", "TIMESTAMP WITHOUT TIME ZONE"),
        ("grace_state", "VARCHAR(16)"),
    ],
    "broadcasts": [
        ("segment_params", "JSON"),
        ("credit_days", "INTEGER"),
    ],
}

NEW_TABLES = ("trials", "refund_requests", "promo_redemptions", "promo_codes", "payment_methods")


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    for table, cols in NEW_COLUMNS.items():
        for col, ddl in cols:
            op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl} NULL")

    if not _has_table("payment_methods"):
        op.create_table(
            "payment_methods",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "telegram_user_id", sa.BigInteger(),
                sa.ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("provider", sa.String(length=32), nullable=False, server_default="yookassa"),
            sa.Column("external_id", sa.String(length=128), nullable=False),
            sa.Column("title", sa.String(length=64), nullable=True),
            sa.Column("card_fingerprint", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("provider", "external_id", name="uq_payment_methods_provider_external"),
        )
        op.create_index("ix_payment_methods_telegram_user_id", "payment_methods", ["telegram_user_id"])

    if not _has_table("promo_codes"):
        op.create_table(
            "promo_codes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("kind", sa.String(length=24), nullable=False),
            sa.Column("plan_code", sa.String(length=32), nullable=True),
            sa.Column("days", sa.Integer(), nullable=True),
            sa.Column("discount_percent", sa.Integer(), nullable=True),
            sa.Column("audience", sa.String(length=24), nullable=False, server_default="all"),
            sa.Column("max_uses", sa.Integer(), nullable=True),
            sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("per_user_limit", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("valid_from", sa.DateTime(), nullable=True),
            sa.Column("valid_until", sa.DateTime(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by", sa.BigInteger(), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("code", name="uq_promo_codes_code"),
        )

    if not _has_table("promo_redemptions"):
        op.create_table(
            "promo_redemptions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "promo_code_id", sa.Integer(),
                sa.ForeignKey("promo_codes.id", ondelete="CASCADE"), nullable=True,
            ),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column(
                "telegram_user_id", sa.BigInteger(),
                sa.ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="applied"),
            sa.Column("reward", sa.JSON(), nullable=True),
            sa.Column("redeemed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_promo_redemptions_promo_code_id", "promo_redemptions", ["promo_code_id"])
        op.create_index("ix_promo_redemptions_telegram_user_id", "promo_redemptions", ["telegram_user_id"])
        op.create_index("ix_promo_redemptions_code_user", "promo_redemptions", ["code", "telegram_user_id"])

    if not _has_table("refund_requests"):
        op.create_table(
            "refund_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "payment_id", sa.Integer(),
                sa.ForeignKey("payments.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "telegram_user_id", sa.BigInteger(),
                sa.ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("amount", sa.Numeric(10, 2), nullable=True),
            sa.Column("decided_by", sa.BigInteger(), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("admin_chat_id", sa.BigInteger(), nullable=True),
            sa.Column("admin_message_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_refund_requests_payment_id", "refund_requests", ["payment_id"])
        op.create_index("ix_refund_requests_telegram_user_id", "refund_requests", ["telegram_user_id"])
        op.create_index(
            "uq_refund_requests_open_per_payment", "refund_requests", ["payment_id"],
            unique=True, postgresql_where=sa.text("status = 'pending'"),
        )

    if not _has_table("trials"):
        op.create_table(
            "trials",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "telegram_user_id", sa.BigInteger(),
                sa.ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="trial"),
            sa.Column("plan_code", sa.String(length=32), nullable=False),
            sa.Column("days", sa.Integer(), nullable=False),
            sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="SET NULL"), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("ends_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("telegram_user_id", name="uq_trials_telegram_user"),
        )


def downgrade() -> None:
    for table in NEW_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for table, cols in NEW_COLUMNS.items():
        for col, _ in cols:
            op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}")
