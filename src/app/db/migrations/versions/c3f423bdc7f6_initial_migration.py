"""Initial migration

Revision ID: c3f423bdc7f6
Revises: 
Create Date: 2025-12-24 18:11:22.113129

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3f423bdc7f6'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Создаем таблицу remna_users
    op.create_table(
        'remna_users',
        sa.Column('remna_id', sa.String(length=64), nullable=False, comment='ID пользователя из Remna API'),
        sa.Column('username', sa.String(length=128), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('raw_data', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='Полные данные из Remna API'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True, comment='Последняя синхронизация с Remna API'),
        sa.PrimaryKeyConstraint('remna_id')
    )
    op.create_index(op.f('ix_remna_users_username'), 'remna_users', ['username'], unique=False)
    op.create_index(op.f('ix_remna_users_email'), 'remna_users', ['email'], unique=False)
    
    # Создаем таблицу telegram_users
    op.create_table(
        'telegram_users',
        sa.Column('telegram_id', sa.BigInteger(), nullable=False, comment='Telegram User ID'),
        sa.Column('username', sa.String(length=64), nullable=True),
        sa.Column('first_name', sa.String(length=128), nullable=True),
        sa.Column('last_name', sa.String(length=128), nullable=True),
        sa.Column('language_code', sa.String(length=10), nullable=True),
        sa.Column('remna_user_id', sa.String(length=64), nullable=True, comment='Связь с пользователем Remna'),
        sa.Column('is_admin', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(), nullable=True, comment='Последняя активность в боте'),
        sa.ForeignKeyConstraint(['remna_user_id'], ['remna_users.remna_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('telegram_id')
    )
    op.create_index(op.f('ix_telegram_users_username'), 'telegram_users', ['username'], unique=False)
    op.create_index(op.f('ix_telegram_users_is_admin'), 'telegram_users', ['is_admin'], unique=False)
    op.create_index(op.f('ix_telegram_users_remna_user_id'), 'telegram_users', ['remna_user_id'], unique=False)
    
    # Создаем таблицу subscriptions
    op.create_table(
        'subscriptions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
        sa.Column('remna_user_id', sa.String(length=64), nullable=True),
        sa.Column('plan_code', sa.String(length=32), nullable=False, comment='Код тарифа: basic, premium, pro'),
        sa.Column('plan_name', sa.String(length=128), nullable=True, comment='Название тарифа'),
        sa.Column('active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('valid_until', sa.DateTime(), nullable=True),
        sa.Column('config_data', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='Данные конфигурации VPN'),
        sa.Column('remna_subscription_id', sa.String(length=64), nullable=True, comment='ID подписки в Remna API'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['remna_user_id'], ['remna_users.remna_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['telegram_user_id'], ['telegram_users.telegram_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_subscriptions_telegram_user_id'), 'subscriptions', ['telegram_user_id'], unique=False)
    op.create_index(op.f('ix_subscriptions_remna_user_id'), 'subscriptions', ['remna_user_id'], unique=False)
    op.create_index(op.f('ix_subscriptions_plan_code'), 'subscriptions', ['plan_code'], unique=False)
    op.create_index(op.f('ix_subscriptions_active'), 'subscriptions', ['active'], unique=False)
    op.create_index(op.f('ix_subscriptions_valid_until'), 'subscriptions', ['valid_until'], unique=False)
    op.create_index(op.f('ix_subscriptions_remna_subscription_id'), 'subscriptions', ['remna_subscription_id'], unique=False)
    
    # Создаем таблицу payments
    op.create_table(
        'payments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
        sa.Column('provider', sa.String(length=16), server_default='yookassa', nullable=False, comment='Провайдер платежей'),
        sa.Column('external_id', sa.String(length=128), nullable=False, comment='ID платежа во внешней системе'),
        sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='RUB', nullable=False),
        sa.Column('status', sa.String(length=24), server_default='pending', nullable=False, comment='pending, succeeded, canceled, failed'),
        sa.Column('subscription_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('payment_metadata', postgresql.JSON(astext_type=sa.Text()), nullable=True, comment='Дополнительные данные платежа'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('paid_at', sa.DateTime(), nullable=True, comment='Время успешной оплаты'),
        sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['telegram_user_id'], ['telegram_users.telegram_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_id')
    )
    op.create_index(op.f('ix_payments_telegram_user_id'), 'payments', ['telegram_user_id'], unique=False)
    op.create_index(op.f('ix_payments_provider'), 'payments', ['provider'], unique=False)
    op.create_index(op.f('ix_payments_external_id'), 'payments', ['external_id'], unique=False)
    op.create_index(op.f('ix_payments_status'), 'payments', ['status'], unique=False)
    op.create_index(op.f('ix_payments_created_at'), 'payments', ['created_at'], unique=False)
    op.create_index(op.f('ix_payments_subscription_id'), 'payments', ['subscription_id'], unique=False)
    
    # Создаем таблицу squads
    op.create_table(
        'squads',
        sa.Column('remna_id', sa.String(length=64), nullable=False, comment='ID сквада из Remna API'),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('raw_data', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('remna_id')
    )
    
    # Создаем таблицу nodes
    op.create_table(
        'nodes',
        sa.Column('remna_id', sa.String(length=64), nullable=False, comment='ID ноды из Remna API'),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('location', sa.String(length=128), nullable=True),
        sa.Column('raw_data', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('remna_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('nodes')
    op.drop_table('squads')
    op.drop_index(op.f('ix_payments_subscription_id'), table_name='payments')
    op.drop_index(op.f('ix_payments_created_at'), table_name='payments')
    op.drop_index(op.f('ix_payments_status'), table_name='payments')
    op.drop_index(op.f('ix_payments_external_id'), table_name='payments')
    op.drop_index(op.f('ix_payments_provider'), table_name='payments')
    op.drop_index(op.f('ix_payments_telegram_user_id'), table_name='payments')
    op.drop_table('payments')
    op.drop_index(op.f('ix_subscriptions_remna_subscription_id'), table_name='subscriptions')
    op.drop_index(op.f('ix_subscriptions_valid_until'), table_name='subscriptions')
    op.drop_index(op.f('ix_subscriptions_active'), table_name='subscriptions')
    op.drop_index(op.f('ix_subscriptions_plan_code'), table_name='subscriptions')
    op.drop_index(op.f('ix_subscriptions_remna_user_id'), table_name='subscriptions')
    op.drop_index(op.f('ix_subscriptions_telegram_user_id'), table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_index(op.f('ix_telegram_users_remna_user_id'), table_name='telegram_users')
    op.drop_index(op.f('ix_telegram_users_is_admin'), table_name='telegram_users')
    op.drop_index(op.f('ix_telegram_users_username'), table_name='telegram_users')
    op.drop_table('telegram_users')
    op.drop_index(op.f('ix_remna_users_email'), table_name='remna_users')
    op.drop_index(op.f('ix_remna_users_username'), table_name='remna_users')
    op.drop_table('remna_users')
