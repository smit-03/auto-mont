"""Add workflow_id scoping column to monitors.

Phase 2 deliverable 6 (outcome assertion monitors). An outcome assertion —
"this workflow should write at least 1 record" — has to name the workflow it
asserts against, and the monitors table had no way to express that: it scopes to
a workspace and optionally an integration, never to a workflow.

Deliberately a column rather than a key inside the existing expected_outcome
JSONB. JSONB carries no schema validation, so a caller writing `workflowId`
instead of `workflow_id` would be accepted and stored, and the monitor would
then silently match nothing while appearing correctly configured. A monitor that
looks healthy and watches nothing is precisely the silent failure this product
sells itself on catching, so it must not be possible to build one by typo.

Nullable, no backfill: existing heartbeat monitors are not workflow-scoped and
NULL is their correct value. This amends the Blueprint's monitors DDL, which has
no workflow scoping at all. See DECISIONS.md 2026-08-15.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers
revision = "20260815_000009"
down_revision = "20260814_000008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable workflow_id scoping column."""
    op.add_column(
        "monitors",
        sa.Column("workflow_id", sa.String(length=255), nullable=True),
    )

    # Partial index: only outcome/schema monitors ever populate this column, so
    # indexing the NULL rows would be pure overhead. Supports the reverse lookup
    # ("which monitors watch workflow X") if outcome assertions later move inline
    # into the polling path, where that question is asked once per execution.
    op.create_index(
        "idx_monitors_workflow_scope",
        "monitors",
        ["integration_id", "workflow_id"],
        postgresql_where=sa.text("workflow_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Drop the workflow_id column and its index."""
    op.drop_index("idx_monitors_workflow_scope", table_name="monitors")
    op.drop_column("monitors", "workflow_id")
