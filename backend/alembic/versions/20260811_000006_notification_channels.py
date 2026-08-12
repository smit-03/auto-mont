"""Add per-workspace notification channels.

Alerts were delivered to a single global Slack webhook read from application
settings, so every workspace's alerts — workflow names, error messages, failure
patterns — landed in one channel. That is a cross-tenant data leak: the data
layer was correctly isolated per workspace and the delivery layer broadcast all
of it to one destination.

This table gives each workspace its own destinations. The webhook URL is a
credential (anyone holding it can post into that customer's channel) and is
stored encrypted with the same per-workspace AES-256-GCM scheme used for
integration API keys.

No backfill: there is deliberately no migration of the global SLACK_WEBHOOK_URL
into per-workspace rows, because assigning one tenant's webhook to every
workspace would recreate the leak this removes. Workspaces configure their own
channel via POST /api/v1/channels; outside production, delivery falls back to
SLACK_WEBHOOK_URL so local development still works.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision identifiers
revision = "20260811_000006"
down_revision = "20260811_000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the notification_channels table."""
    op.create_table(
        "notification_channels",
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
        sa.Column("channel_type", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("destination_enc", postgresql.BYTEA(), nullable=False),
        sa.Column("destination_hint", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("min_severity", sa.Text(), nullable=False, server_default="warning"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_notification_channels_workspace_id",
        "notification_channels",
        ["workspace_id"],
    )
    op.create_index(
        "idx_channels_workspace_active",
        "notification_channels",
        ["workspace_id", "enabled"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Drop the notification_channels table (indexes go with it)."""
    op.drop_table("notification_channels")
