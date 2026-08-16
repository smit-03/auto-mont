"""Add workflow_schemas table for schema drift detection.

Phase 2, failure taxonomy class 4 (Data Quality & Schema). Stores one row per
(integration, workflow) holding the field shape of the last successful run, so
the next successful run can be compared against it.

The UNIQUE on (integration_id, workflow_id) is load-bearing, not decorative: the
drift check reads the baseline and then writes it back, which races between two
concurrent polls of the same integration. Without the constraint both polls
insert and the workflow ends up with two competing baselines that alternately
report the other's shape as drift.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision identifiers
revision = "20260814_000008"
down_revision = "20260812_000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the workflow_schemas table."""
    op.create_table(
        "workflow_schemas",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_id", sa.String(length=255), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "fields",
            postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("sample_run_id", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "integration_id",
            "workflow_id",
            name="uq_workflow_schemas_integration_workflow",
        ),
    )

    op.create_index(
        "ix_workflow_schemas_workspace_id",
        "workflow_schemas",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workflow_schemas_integration_id",
        "workflow_schemas",
        ["integration_id"],
    )
    op.create_index(
        "idx_workflow_schemas_workspace",
        "workflow_schemas",
        ["workspace_id", "workflow_id"],
    )


def downgrade() -> None:
    """Drop the workflow_schemas table (indexes and constraint go with it)."""
    op.drop_table("workflow_schemas")
