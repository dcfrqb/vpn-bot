"""Add referral_payouts ledger + move referral_payout rows out of payments

Выносим ручные реферальные выплаты (provider='referral_payout') из таблицы
payments в отдельный леджер referral_payouts. Причина: 0₽-запись со
status='succeeded' воспринималась платёжными воркерами как реальная покупка
и перепровизинивалась (баг 0₽ → basic + повторное "оплата подтверждена").

upgrade: создаёт таблицу, переносит существующие referral_payout-записи,
удаляет их из payments.
downgrade: возвращает записи в payments (external_id восстанавливается
детерминированно из created_at+admin_id, не байт-в-байт исходный — он нигде
не используется как ключ соединения, только LIKE-префикс).

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-06-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "referral_payouts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "admin_id",
            sa.BigInteger(),
            sa.ForeignKey("telegram_users.telegram_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "promo_code", sa.String(length=32), nullable=False, server_default="sun718",
        ),
        sa.Column("payout_months", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_referral_payouts_admin_id", "referral_payouts", ["admin_id"])
    op.create_index("ix_referral_payouts_promo_code", "referral_payouts", ["promo_code"])
    op.create_index("ix_referral_payouts_created_at", "referral_payouts", ["created_at"])

    # Перенос существующих выплат из payments в леджер.
    op.execute(
        """
        INSERT INTO referral_payouts (admin_id, promo_code, payout_months, note, created_at)
        SELECT
            telegram_user_id,
            COALESCE(payment_metadata->>'promo_code', 'sun718'),
            COALESCE((payment_metadata->>'payout_months')::int, 0),
            payment_metadata->>'note',
            COALESCE(paid_at, created_at)
        FROM payments
        WHERE provider = 'referral_payout'
          AND external_id LIKE 'referral_payout_sun718_%'
        """
    )
    op.execute(
        """
        DELETE FROM payments
        WHERE provider = 'referral_payout'
          AND external_id LIKE 'referral_payout_sun718_%'
        """
    )


def downgrade() -> None:
    # Возврат выплат в payments. external_id восстанавливается детерминированно.
    op.execute(
        """
        INSERT INTO payments (
            telegram_user_id, provider, external_id, amount, currency, status,
            description, paid_at, payment_metadata, created_at, updated_at
        )
        SELECT
            admin_id,
            'referral_payout',
            'referral_payout_sun718_'
                || CAST(EXTRACT(EPOCH FROM created_at)::bigint AS text)
                || '_' || CAST(admin_id AS text),
            0, 'RUB', 'succeeded',
            'Sun718 payout: ' || payout_months || ' мес ('
                || COALESCE(note, 'без комментария') || ')',
            created_at,
            json_build_object(
                'promo_code', promo_code,
                'payout_months', payout_months,
                'note', COALESCE(note, ''),
                'admin_id', admin_id
            ),
            created_at, now()
        FROM referral_payouts
        WHERE admin_id IS NOT NULL
        """
    )
    op.drop_index("ix_referral_payouts_created_at", table_name="referral_payouts")
    op.drop_index("ix_referral_payouts_promo_code", table_name="referral_payouts")
    op.drop_index("ix_referral_payouts_admin_id", table_name="referral_payouts")
    op.drop_table("referral_payouts")
