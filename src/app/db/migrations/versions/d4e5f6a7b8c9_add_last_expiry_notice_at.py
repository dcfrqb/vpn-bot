"""Add last_expiry_notice_at to subscriptions for deduplication

Revision ID: d4e5f6a7b8c9
Revises: 8bbcc038627b
Create Date: 2025-02-12

Дедупликация уведомлений об истечении подписки: максимум 1 уведомление
раз в 24 часа на (user_id, subscription_id).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = '0a27e6958972'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'subscriptions',
        sa.Column(
            'last_expiry_notice_at',
            sa.DateTime(),
            nullable=True,
            comment='Время последнего уведомления об истечении (rate-limit 24h)'
        )
    )


def downgrade() -> None:
    op.drop_column('subscriptions', 'last_expiry_notice_at')
