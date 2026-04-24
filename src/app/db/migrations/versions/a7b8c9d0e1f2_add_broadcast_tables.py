"""Add broadcast tables + opt-out/is_active on telegram_users

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # telegram_users: is_active + broadcast_opt_out
    op.add_column(
        "telegram_users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
            comment="False если бот получил TelegramForbiddenError",
        ),
    )
    op.add_column(
        "telegram_users",
        sa.Column(
            "broadcast_opt_out",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
            comment="True если юзер отписался от рассылок",
        ),
    )
    op.create_index(
        "ix_telegram_users_is_active", "telegram_users", ["is_active"],
    )
    op.create_index(
        "ix_telegram_users_broadcast_opt_out", "telegram_users", ["broadcast_opt_out"],
    )

    # broadcasts
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("text_html", sa.Text(), nullable=False),
        sa.Column("photo_file_id", sa.String(length=256), nullable=True),
        sa.Column("buttons_json", sa.JSON(), nullable=True),
        sa.Column(
            "segment", sa.String(length=16), nullable=False, server_default="all",
        ),
        sa.Column(
            "disable_notification", sa.Boolean(), nullable=False, server_default=sa.false(),
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_broadcasts_created_by", "broadcasts", ["created_by"])
    op.create_index("ix_broadcasts_created_at", "broadcasts", ["created_at"])
    op.create_index("ix_broadcasts_started_at", "broadcasts", ["started_at"])
    op.create_index("ix_broadcasts_finished_at", "broadcasts", ["finished_at"])

    # broadcast_recipients
    op.create_table(
        "broadcast_recipients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "broadcast_id",
            sa.Integer(),
            sa.ForeignKey("broadcasts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_telegram_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_users.telegram_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending",
        ),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "broadcast_id", "user_telegram_id", name="uq_broadcast_recipient",
        ),
    )
    op.create_index(
        "ix_broadcast_recipients_broadcast_id", "broadcast_recipients", ["broadcast_id"],
    )
    op.create_index(
        "ix_broadcast_recipients_user_telegram_id",
        "broadcast_recipients",
        ["user_telegram_id"],
    )
    op.create_index(
        "ix_broadcast_recipients_bc_status",
        "broadcast_recipients",
        ["broadcast_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_broadcast_recipients_bc_status", table_name="broadcast_recipients",
    )
    op.drop_index(
        "ix_broadcast_recipients_user_telegram_id", table_name="broadcast_recipients",
    )
    op.drop_index(
        "ix_broadcast_recipients_broadcast_id", table_name="broadcast_recipients",
    )
    op.drop_table("broadcast_recipients")

    op.drop_index("ix_broadcasts_finished_at", table_name="broadcasts")
    op.drop_index("ix_broadcasts_started_at", table_name="broadcasts")
    op.drop_index("ix_broadcasts_created_at", table_name="broadcasts")
    op.drop_index("ix_broadcasts_created_by", table_name="broadcasts")
    op.drop_table("broadcasts")

    op.drop_index("ix_telegram_users_broadcast_opt_out", table_name="telegram_users")
    op.drop_index("ix_telegram_users_is_active", table_name="telegram_users")
    op.drop_column("telegram_users", "broadcast_opt_out")
    op.drop_column("telegram_users", "is_active")
