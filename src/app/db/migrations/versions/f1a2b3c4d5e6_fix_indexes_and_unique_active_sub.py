"""Fix indexes and partial-unique on active subscription

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-04-24

Закрывает CRITICAL из ревью:
- Partial unique index (telegram_user_id) WHERE active=true — защита от двух
  активных подписок у одного юзера при гонке.

Закрывает MEDIUM:
- Индексы на subscriptions.updated_at и payments.updated_at для фоновых задач.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Partial unique — только для active=true.
    # Если в БД уже есть несколько active подписок у одного юзера (артефакт гонки),
    # миграция упадёт с IntegrityError — это ОК, требует ручной чистки перед upgrade.
    op.create_index(
        "uq_active_subscription_per_user",
        "subscriptions",
        ["telegram_user_id"],
        unique=True,
        postgresql_where="active = true",
    )
    op.create_index(
        "ix_subscriptions_updated_at",
        "subscriptions",
        ["updated_at"],
    )
    op.create_index(
        "ix_payments_updated_at",
        "payments",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_payments_updated_at", table_name="payments")
    op.drop_index("ix_subscriptions_updated_at", table_name="subscriptions")
    op.drop_index("uq_active_subscription_per_user", table_name="subscriptions")
