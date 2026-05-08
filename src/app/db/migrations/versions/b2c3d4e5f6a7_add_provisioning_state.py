"""Add provisioning_state to subscriptions

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-05-08

Закрывает баг "оплата прошла, подписка не выдалась" (split-state БД↔Remnawave).

Новые поля subscriptions:
- provisioning_state: pending | synced | failed
- remnawave_synced_at: время последнего успешного апдейта Remnawave
- remnawave_expected_expire_at: какой expireAt мы передавали в Remnawave
- last_provisioning_attempt_at: время последней попытки sync (для backoff)
- last_provisioning_error: текст последней ошибки sync (диагностика)

Backfill: всем active=true подпискам ставим 'synced' с remnawave_synced_at=updated_at,
чтобы reconciler не пытался лечить уже работающие подписки. Сломанные подписки
помечаются 'failed' разовым скриптом scripts/audit_remnawave_desync.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column(
            "provisioning_state",
            sa.String(length=24),
            server_default=sa.text("'pending'"),
            nullable=False,
            comment="pending | synced | failed",
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "remnawave_synced_at",
            sa.DateTime(),
            nullable=True,
            comment="Время последнего успешного sync Remnawave",
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "remnawave_expected_expire_at",
            sa.DateTime(),
            nullable=True,
            comment="Какой expireAt мы передавали в Remnawave (intent)",
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "last_provisioning_attempt_at",
            sa.DateTime(),
            nullable=True,
            comment="Время последней попытки sync (для backoff)",
        ),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "last_provisioning_error",
            sa.Text(),
            nullable=True,
            comment="Текст последней ошибки sync",
        ),
    )

    # Индекс для reconciler'а: быстро находить pending/failed подписки.
    op.create_index(
        "ix_subscriptions_provisioning_state_valid_until",
        "subscriptions",
        ["provisioning_state", "valid_until"],
    )

    # Backfill: всем активным подпискам ставим synced. is_lifetime тоже synced.
    # Те, что не active — оставляем pending (они не должны мешать, reconciler их игнорирует
    # т.к. фильтрует по active=true).
    op.execute(
        """
        UPDATE subscriptions
        SET provisioning_state = 'synced',
            remnawave_synced_at = COALESCE(updated_at, created_at),
            remnawave_expected_expire_at = valid_until
        WHERE active = true OR is_lifetime = true
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_subscriptions_provisioning_state_valid_until",
        table_name="subscriptions",
    )
    op.drop_column("subscriptions", "last_provisioning_error")
    op.drop_column("subscriptions", "last_provisioning_attempt_at")
    op.drop_column("subscriptions", "remnawave_expected_expire_at")
    op.drop_column("subscriptions", "remnawave_synced_at")
    op.drop_column("subscriptions", "provisioning_state")
