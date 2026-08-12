"""Add monitors.ping_token as an indexed, equality-matched column.

The public /ping/{token} endpoint previously located a monitor with
``ping_url LIKE '%token%'``. That was an unindexed substring scan on every
unauthenticated request, it could match the wrong monitor when one token was a
substring of another monitor's URL, and it raised MultipleResultsFound (a 500 on
a public endpoint) when more than one row matched.

This adds the token as its own column, backfills it out of the existing
ping_url values, and enforces uniqueness. ping_url is retained as the
display/copy value shown in the dashboard.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers
revision = "20260811_000004"
down_revision = "20260722_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ping_token, backfill from ping_url, then index it uniquely."""
    op.add_column(
        "monitors",
        sa.Column("ping_token", sa.String(length=64), nullable=True),
    )

    # ping_url has the shape '/ping/<token>', so splitting on '/' yields
    # ['', 'ping', '<token>'] and the token is the third part.
    op.execute(
        """
        UPDATE monitors
        SET ping_token = split_part(ping_url, '/', 3)
        WHERE ping_url IS NOT NULL
          AND split_part(ping_url, '/', 3) <> ''
        """
    )

    op.create_index(
        "ix_monitors_ping_token",
        "monitors",
        ["ping_token"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the index and the column; ping_url still carries the token."""
    op.drop_index("ix_monitors_ping_token", table_name="monitors")
    op.drop_column("monitors", "ping_token")
