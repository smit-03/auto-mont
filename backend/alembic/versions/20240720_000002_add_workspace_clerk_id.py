"""Add clerk_id to workspaces for Clerk identity mapping.

Adds a nullable, unique, indexed string column so an authenticated request
can resolve (or auto-provision) a Workspace by its Clerk identifier
(org_id when present, else the user's sub) instead of forcing the Clerk ID
into the UUID primary key.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers
revision = "20240720_000002"
down_revision = "20240630_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add workspaces.clerk_id with a unique index."""
    op.add_column(
        "workspaces",
        sa.Column("clerk_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_workspaces_clerk_id",
        "workspaces",
        ["clerk_id"],
        unique=True,
    )


def downgrade() -> None:
    """Drop workspaces.clerk_id and its index."""
    op.drop_index("ix_workspaces_clerk_id", table_name="workspaces")
    op.drop_column("workspaces", "clerk_id")
