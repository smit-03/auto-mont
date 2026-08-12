"""Move alert deduplication from notification-level to incident-level.

Alerts were inserted once per detection. Because deduplication lived only in the
notification dispatcher's Redis check, a monitor that stayed down produced one
Slack message per hour (correct) and one alert row per beat tick — once a
minute, indefinitely (not correct). The alerts table grew without bound and the
dashboard feed degenerated into many copies of a single incident.

This adds incident identity to the table:

* ``dedup_key``        — stable identity of the incident, excluding anything
                         that varies per detection.
* ``occurrence_count`` — how many times this incident has been observed.
* ``last_seen_at``     — when it was most recently observed.
* a PARTIAL unique index on (workspace_id, dedup_key) WHERE resolved_at IS NULL,
  so at most one *open* incident exists per key while resolved incidents leave
  the index and allow a recurrence to open a new one.

Existing rows are backfilled with a per-row unique dedup_key derived from their
own id, so historical alerts stay distinct and cannot collide under the new
index.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers
revision = "20260811_000005"
down_revision = "20260811_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add incident columns, backfill, then add the partial unique index."""
    op.add_column("alerts", sa.Column("dedup_key", sa.Text(), nullable=True))
    op.add_column(
        "alerts",
        sa.Column(
            "occurrence_count", sa.Integer(), nullable=False, server_default="1"
        ),
    )
    op.add_column(
        "alerts",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Backfill before indexing. Historical rows have no incident identity, and
    # collapsing them retroactively would be a guess — give each its own key so
    # the unique index can be created without conflict and history is preserved
    # exactly as recorded.
    op.execute(
        """
        UPDATE alerts
        SET dedup_key = 'legacy:' || id::text,
            last_seen_at = created_at
        WHERE dedup_key IS NULL
        """
    )

    op.create_index(
        "uq_alerts_open_incident",
        "alerts",
        ["workspace_id", "dedup_key"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    """Drop the incident index and columns."""
    op.drop_index("uq_alerts_open_incident", table_name="alerts")
    op.drop_column("alerts", "last_seen_at")
    op.drop_column("alerts", "occurrence_count")
    op.drop_column("alerts", "dedup_key")
