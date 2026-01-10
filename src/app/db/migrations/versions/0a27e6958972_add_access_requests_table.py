"""add_access_requests_table

Revision ID: 0a27e6958972
Revises: 8bbcc038627b
Create Date: 2025-01-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0a27e6958972'
down_revision: Union[str, Sequence[str], None] = '8bbcc038627b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создает таблицу access_requests для запросов на выдачу VPN-доступа"""
    op.create_table(
        'access_requests',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False, comment='Telegram ID пользователя, запросившего доступ'),
        sa.Column('name', sa.String(length=255), nullable=True, comment='Имя пользователя'),
        sa.Column('username', sa.String(length=64), nullable=True, comment='Username пользователя'),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False, comment='Статус: pending, approved, rejected'),
        sa.Column('requested_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='Время создания запроса'),
        sa.Column('approved_at', sa.DateTime(), nullable=True, comment='Время одобрения/отклонения запроса'),
        sa.Column('approved_by', sa.BigInteger(), nullable=True, comment='Telegram ID администратора, обработавшего запрос'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['telegram_id'], ['telegram_users.telegram_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_access_requests_telegram_id'), 'access_requests', ['telegram_id'], unique=False)
    op.create_index(op.f('ix_access_requests_status'), 'access_requests', ['status'], unique=False)
    op.create_index(op.f('ix_access_requests_requested_at'), 'access_requests', ['requested_at'], unique=False)
    op.create_index(op.f('ix_access_requests_approved_by'), 'access_requests', ['approved_by'], unique=False)


def downgrade() -> None:
    """Удаляет таблицу access_requests"""
    op.drop_index(op.f('ix_access_requests_approved_by'), table_name='access_requests')
    op.drop_index(op.f('ix_access_requests_requested_at'), table_name='access_requests')
    op.drop_index(op.f('ix_access_requests_status'), table_name='access_requests')
    op.drop_index(op.f('ix_access_requests_telegram_id'), table_name='access_requests')
    op.drop_table('access_requests')
