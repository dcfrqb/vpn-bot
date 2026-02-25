"""Add is_lifetime to subscriptions

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2025-02-24

Флаг подписки "навсегда" (admin grant forever).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column(
            "is_lifetime",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Подписка навсегда (admin grant forever)",
        ),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "is_lifetime")
