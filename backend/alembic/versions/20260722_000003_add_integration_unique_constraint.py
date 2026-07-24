"""Add unique constraint for active integrations per workspace.

Prevents duplicate active integrations for (workspace_id, platform,
display_name) while still allowing the same combination to be reused
after a soft delete (deleted_at IS NOT NULL rows are excluded).
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers
revision = "20260722_000003"
down_revision = "20240720_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add partial unique index on active (workspace_id, platform, display_name)."""
    op.create_index(
        "uq_integrations_workspace_platform_name_active",
        "integrations",
        ["workspace_id", "platform", "display_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Drop the partial unique index."""
    op.drop_index(
        "uq_integrations_workspace_platform_name_active",
        table_name="integrations",
    )
