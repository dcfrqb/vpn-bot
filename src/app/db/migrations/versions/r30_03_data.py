"""Release 3.0: data backfills (idempotent, safe on prod)

Revision ID: r30_03_data
Revises: r30_02_constraints
Create Date: 2026-09-23

Every statement is idempotent (WHERE guards on the not-yet-done condition),
so re-running this revision (e.g. `alembic upgrade head` twice, or on a DB
that already has some of this done) is a no-op the second time. Read-only
prod checks (07_database.md + this stream's own queries, 2026-09-23) are
quoted in each step.

1. Payment id 2 (`external_id='test_e2e_003'`, 99 RUB, 2026-03-09) is e2e
   test junk counted in revenue (07 §2.3 Q10). Relabel `provider -> 'test'`
   instead of deleting, so the payment row and any FK from it survive.
   Requirement handed to stream E in impl/requests/F_to_E.md: exclude
   provider='test' from services/stats.py and the admin revenue message.
2. Backfill payments.plan_code / period_months / kind / method from
   payment_metadata (JSON, legacy). Verified paths on prod:
   - plan_code:      metadata->>'plan_code', falling back to 'trial' when
                      metadata->>'tariff' starts with 'trial' (23 promo rows
                      have no top-level plan_code, see 07 §2.3 and this
                      stream's own query of provider='promo' rows)
   - period_months:  metadata->>'period_months' (numeric string on every
                      yookassa row that has it)
   - kind:            'promo' for provider='promo', 'obhod_package' for a
                      2.x obhod package (plan_code obhod_*), else 'subscription'
   - method:          'yookassa' for provider in ('yookassa','test'), else
                      left NULL (promo grants have no payment method)
   - card_fingerprint: metadata->'last_webhook'->'object'->'payment_method'
                      ->'card' (first6/last4/expiry), the actual path in the
                      YooKassa webhook envelope stored by 2.x code (NOT
                      metadata->'payment_method'->'card', which is empty --
                      07's "105 rows contain payment_method.card" refers to
                      this nested path, confirmed by direct query: 105 rows).
3. Backfill trials from provider='promo' payments with
   metadata->>'promo_code' = 'trial' (the only import from Foundation's
   `trials` table with a fixed 'trial' source; other promo codes are
   redemptions, not trials, and belong to promo_redemptions which stream E
   backfills itself since it owns PromoService). started_at = payments.paid_at
   (fallback created_at), days parsed from metadata->>'tariff'
   ('trial_standard_10d' -> 10, 'trial_10d' -> 10), ends_at =
   started_at + days. Guarded by ON CONFLICT DO NOTHING on the
   uq_trials_telegram_user unique constraint (idempotent, and harmless if a
   real /trial already wrote a row by the time this runs).
4. Delete draft broadcast id 1: `total=0`, `started_at IS NULL` (07 §2.1,
   §2.6) -- guarded by exactly that WHERE so it is a no-op if id 1 does not
   match (e.g. already deleted, or reused on a fresh/test DB).
5. Normalize subscriptions.plan_name from plan_code via the domain catalog
   display strings (values as in app/domain/plans.py PLAN_CATALOG; kept as
   a literal CASE here, not an import, because migrations must not depend on
   application code that can change shape after this revision is written).
   plan_name stays untouched (NULL) for any plan_code migrations does not
   recognise, rather than guessing.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "r30_03_data"
down_revision: Union[str, Sequence[str], None] = "r30_02_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PLAN_NAME_CASE = """
    CASE plan_code
        WHEN 'basic' THEN 'Базовый тариф'
        WHEN 'premium' THEN 'Премиум тариф'
        WHEN 'lite' THEN 'Lite'
        WHEN 'standard' THEN 'Standard'
        WHEN 'pro' THEN 'Pro'
        WHEN 'trial' THEN 'Пробный период'
        WHEN 'obhod' THEN 'Обход'
        ELSE plan_name
    END
"""


def upgrade() -> None:
    # 1. test payment relabeled, not deleted
    op.execute(
        "UPDATE payments SET provider = 'test' "
        "WHERE id = 2 AND external_id = 'test_e2e_003' AND provider = 'yookassa'"
    )

    # 2. payments.plan_code / period_months / kind / method / card_fingerprint
    op.execute(
        """
        UPDATE payments
        SET plan_code = COALESCE(
            payment_metadata::jsonb ->> 'plan_code',
            CASE WHEN payment_metadata::jsonb ->> 'tariff' LIKE 'trial%' THEN 'trial' END
        )
        WHERE plan_code IS NULL
          AND payment_metadata IS NOT NULL
          AND COALESCE(
                payment_metadata::jsonb ->> 'plan_code',
                CASE WHEN payment_metadata::jsonb ->> 'tariff' LIKE 'trial%' THEN 'trial' END
              ) IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE payments
        SET period_months = (payment_metadata::jsonb ->> 'period_months')::integer
        WHERE period_months IS NULL
          AND payment_metadata IS NOT NULL
          AND (payment_metadata::jsonb ->> 'period_months') ~ '^[0-9]+$'
        """
    )
    op.execute(
        """
        UPDATE payments
        SET kind = CASE
            WHEN provider = 'promo' THEN 'promo'
            WHEN payment_metadata::jsonb ->> 'plan_code' LIKE 'obhod\\_%' THEN 'obhod_package'
            ELSE 'subscription'
        END
        WHERE kind IS NULL
        """
    )
    op.execute(
        """
        UPDATE payments
        SET method = 'yookassa'
        WHERE method IS NULL AND provider IN ('yookassa', 'test')
        """
    )
    op.execute(
        """
        UPDATE payments
        SET card_fingerprint =
            (payment_metadata::jsonb -> 'last_webhook' -> 'object' -> 'payment_method' -> 'card' ->> 'first6')
            || '-' ||
            (payment_metadata::jsonb -> 'last_webhook' -> 'object' -> 'payment_method' -> 'card' ->> 'last4')
            || '-' ||
            (payment_metadata::jsonb -> 'last_webhook' -> 'object' -> 'payment_method' -> 'card' ->> 'expiry_month')
            || '/' ||
            right(payment_metadata::jsonb -> 'last_webhook' -> 'object' -> 'payment_method' -> 'card' ->> 'expiry_year', 2)
        WHERE card_fingerprint IS NULL
          AND (payment_metadata::jsonb -> 'last_webhook' -> 'object' -> 'payment_method' -> 'card') IS NOT NULL
        """
    )

    # 3. trials backfilled from 2.x promo='trial' payments
    op.execute(
        """
        INSERT INTO trials (telegram_user_id, source, plan_code, days, payment_id, started_at, ends_at, created_at)
        SELECT
            p.telegram_user_id,
            'backfill',
            'trial',
            CASE
                WHEN (payment_metadata::jsonb ->> 'tariff') ~ '_([0-9]+)d$'
                    THEN substring(payment_metadata::jsonb ->> 'tariff' from '_([0-9]+)d$')::integer
                ELSE 5
            END AS days,
            p.id,
            COALESCE(p.paid_at, p.created_at),
            COALESCE(p.paid_at, p.created_at) + make_interval(
                days => CASE
                    WHEN (payment_metadata::jsonb ->> 'tariff') ~ '_([0-9]+)d$'
                        THEN substring(payment_metadata::jsonb ->> 'tariff' from '_([0-9]+)d$')::integer
                    ELSE 5
                END
            ),
            now()
        FROM payments p
        WHERE p.provider = 'promo'
          AND (p.payment_metadata::jsonb ->> 'promo_code') = 'trial'
        ORDER BY p.id
        ON CONFLICT (telegram_user_id) DO NOTHING
        """
    )

    # 4. draft broadcast, never started
    op.execute("DELETE FROM broadcasts WHERE id = 1 AND total = 0 AND started_at IS NULL")

    # 5. plan_name normalized from plan_code
    op.execute(f"UPDATE subscriptions SET plan_name = {PLAN_NAME_CASE} WHERE plan_code IN "
               "('basic','premium','lite','standard','pro','trial','obhod') "
               f"AND plan_name IS DISTINCT FROM ({PLAN_NAME_CASE})")


def downgrade() -> None:
    # Data migrations are one-way in spirit (07 §6.5 calls this "reviewed by
    # the owner first"). downgrade only undoes what is cheaply reversible and
    # was purely additive; it does not resurrect deleted/relabeled rows.
    op.execute("UPDATE payments SET provider = 'yookassa' WHERE id = 2 AND provider = 'test'")
