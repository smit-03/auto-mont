"""Backfill uq_executions_integration_run_id on databases where it never ran.

This constraint has been part of `20240630_000001` since that migration was
corrected, and it is declared on the `Execution` ORM model's `__table_args__`.
But on any database whose `executions` table was created before that fix — by
an early `Base.metadata.create_all()` run in development, or by a version of
the migration that predates it — Alembic considers `20240630_000001` already
applied and will never re-run it, so the constraint silently never lands
there.

This migration is idempotent and only acts if the constraint is missing, so it
is safe to apply on a database where it already exists (created correctly the
first time) as well as one where it doesn't.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers
revision = "20260812_000007"
down_revision = "20260811_000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the constraint only if it is not already present."""
    conn = op.get_bind()
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint WHERE conname = 'uq_executions_integration_run_id'"
        )
    ).scalar()
    if exists:
        return

    op.create_unique_constraint(
        "uq_executions_integration_run_id",
        "executions",
        ["integration_id", "platform_run_id"],
    )


def downgrade() -> None:
    """Drop the constraint if present."""
    conn = op.get_bind()
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint WHERE conname = 'uq_executions_integration_run_id'"
        )
    ).scalar()
    if not exists:
        return
    op.drop_constraint("uq_executions_integration_run_id", "executions", type_="unique")
