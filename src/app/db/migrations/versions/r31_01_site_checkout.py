"""Website customers and durable checkout ledger, existing tables unchanged."""

from alembic import op
import sqlalchemy as sa

revision = "r31_01_site_checkout"
down_revision = "r30_03_data"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "site_customers",
        sa.Column("account_id", sa.String(36), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True, unique=True),
        sa.Column("backend", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "backend IN ('web', 'telegram')", name="ck_site_customer_backend"
        ),
    )
    op.create_table(
        "site_orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "account_id",
            sa.String(36),
            sa.ForeignKey("site_customers.account_id"),
            nullable=False,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=True, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "state IN ('creating','pending','paid','provisioning','fulfilled','canceled','held','failed','refunded','refunding')",
            name="ck_site_order_state",
        ),
    )
    op.create_index("ix_site_orders_account_id", "site_orders", ["account_id"])
    op.create_index("ix_site_orders_updated_at", "site_orders", ["updated_at"])


def downgrade():
    op.drop_index("ix_site_orders_updated_at", table_name="site_orders")
    op.drop_index("ix_site_orders_account_id", table_name="site_orders")
    op.drop_table("site_orders")
    op.drop_table("site_customers")
