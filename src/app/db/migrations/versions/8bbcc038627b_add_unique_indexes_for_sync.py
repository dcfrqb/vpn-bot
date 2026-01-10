"""add_unique_indexes_for_sync

Revision ID: 8bbcc038627b
Revises: c3f423bdc7f6
Create Date: 2026-01-09 10:33:30.262684

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8bbcc038627b'
down_revision: Union[str, Sequence[str], None] = 'c3f423bdc7f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Добавляет UNIQUE индексы для строгой синхронизации"""
    # UNIQUE индекс на telegram_user_id в subscriptions (одна подписка на пользователя)
    # Создаем уникальный индекс для обеспечения одной активной подписки на пользователя
    op.create_unique_constraint(
        'uq_subscriptions_telegram_user_id',
        'subscriptions',
        ['telegram_user_id'],
    )


def downgrade() -> None:
    """Удаляет UNIQUE индексы"""
    op.drop_constraint('uq_subscriptions_telegram_user_id', 'subscriptions', type_='unique')
