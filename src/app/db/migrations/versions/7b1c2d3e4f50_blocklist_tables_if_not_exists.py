"""Blocklist tables (blocked_users, blocked_cards) IF NOT EXISTS

Revision ID: 7b1c2d3e4f50
Revises: 3f6f6890f801
Create Date: 2026-09-23

Хотфикс 2.1 (07_database §1.2 D1). На проде таблицы стоп-листа созданы вручную
и не описаны ни одной миграцией: БД, собранная из миграций (новый сервер,
стейдж, тесты), их не имела, и стоп-лист молча выключался (ошибки в
services/blocklist.py глотаются).

DDL повторяет живую схему 1-в-1:
  blocked_users(telegram_id bigint PK, reason text, note text,
                blocked_at timestamp NOT NULL DEFAULT now())
  blocked_cards(fingerprint text PK, reason text, note text,
                blocked_at timestamp NOT NULL DEFAULT now())

На проде (таблицы уже есть) upgrade — no-op: CREATE TABLE IF NOT EXISTS.
downgrade намеренно ничего не удаляет: таблицы существовали до миграции,
drop уничтожил бы стоп-лист.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "7b1c2d3e4f50"
down_revision: Union[str, Sequence[str], None] = "3f6f6890f801"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_users (
            telegram_id BIGINT PRIMARY KEY,
            reason TEXT,
            note TEXT,
            blocked_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS blocked_cards (
            fingerprint TEXT PRIMARY KEY,
            reason TEXT,
            note TEXT,
            blocked_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    # Намеренно no-op: см. docstring.
    pass
