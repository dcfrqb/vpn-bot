"""Add sub_kind discriminator for obhod (two-subscription architecture)

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-06-30

Архитектура «две подписки» (основная + обход с лимитом трафика).

Снимает блокеры одной-подписки-на-юзера:
- Колонка-дискриминатор subscriptions.sub_kind (main | obhod), default 'main'.
- Старым строкам проставляется 'main' (бэкфилл в upgrade до создания нового индекса).
- Удаляются ОБА мешающих ограничения уникальности по telegram_user_id:
    * uq_subscriptions_telegram_user_id — НЕ partial UNIQUE constraint
      (мигр. 8bbcc038627b): запрещает любую вторую строку у юзера;
    * uq_active_subscription_per_user — partial unique index WHERE active=true
      (мигр. f1a2b3c4d5e6): запрещает вторую active-подписку.
- Создается новый partial unique index по (telegram_user_id, sub_kind)
  WHERE active=true: один main + один obhod на юзера одновременно, но не два
  одинакового вида.

downgrade: восстанавливает старые ограничения и удаляет sub_kind. Перед
downgrade'ом в БД не должно быть >1 active-строки на юзера (иначе восстановление
uq_active_subscription_per_user упадет IntegrityError — ожидаемо, требует чистки).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "3f6f6890f801"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Колонка-дискриминатор. server_default='main' закрывает существующие строки
    #    на момент ADD COLUMN; явный UPDATE ниже — страховка/документирование намерения.
    op.add_column(
        "subscriptions",
        sa.Column(
            "sub_kind",
            sa.String(length=16),
            server_default=sa.text("'main'"),
            nullable=False,
            comment="main (основная) | obhod (обход с лимитом трафика)",
        ),
    )
    op.execute("UPDATE subscriptions SET sub_kind = 'main' WHERE sub_kind IS NULL")

    op.create_index(
        "ix_subscriptions_sub_kind",
        "subscriptions",
        ["sub_kind"],
    )

    # 2. Снимаем старые ограничения «одна подписка на юзера».
    #    Constraint (8bbcc038627b) — через drop_constraint.
    op.drop_constraint(
        "uq_subscriptions_telegram_user_id",
        "subscriptions",
        type_="unique",
    )
    #    Partial unique index (f1a2b3c4d5e6) — через drop_index.
    op.drop_index(
        "uq_active_subscription_per_user",
        table_name="subscriptions",
    )

    # 3. Новый partial unique index: уникальность пары (telegram_user_id, sub_kind)
    #    среди active=true. Позволяет один main + один obhod одновременно.
    op.create_index(
        "uq_active_subscription_per_user_kind",
        "subscriptions",
        ["telegram_user_id", "sub_kind"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )


def downgrade() -> None:
    # Реверс в обратном порядке.
    op.drop_index(
        "uq_active_subscription_per_user_kind",
        table_name="subscriptions",
    )

    # Старая схема (uq_subscriptions_telegram_user_id) — НЕ partial UNIQUE на
    # telegram_user_id, т.е. одна СТРОКА на юзера всего. obhod-строки ее нарушат,
    # поэтому перед восстановлением удаляем все подписки обхода. Это деструктивно,
    # но downgrade этой миграции означает отказ от фичи «две подписки».
    op.execute("DELETE FROM subscriptions WHERE sub_kind = 'obhod'")

    op.create_index(
        "uq_active_subscription_per_user",
        "subscriptions",
        ["telegram_user_id"],
        unique=True,
        postgresql_where=sa.text("active = true"),
    )
    op.create_unique_constraint(
        "uq_subscriptions_telegram_user_id",
        "subscriptions",
        ["telegram_user_id"],
    )

    op.drop_index("ix_subscriptions_sub_kind", table_name="subscriptions")
    op.drop_column("subscriptions", "sub_kind")
