"""
Initial schema migration for Phase 1 core tables.

Creates: workspaces, integrations, executions, monitors, credentials, alerts, hitl_requests
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision identifiers
revision = "20240630_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create core tables for Phase 1."""

    # Extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS pgvector")

    # Workspaces table
    op.create_table(
        "workspaces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False, server_default="developer"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slug"),
    )

    # Integrations table
    op.create_table(
        "integrations",
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
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("api_key_enc", postgresql.BYTEA(), nullable=True),
        sa.Column("api_key_hint", sa.Text(), nullable=True),
        sa.Column("polling_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("poll_interval_s", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "idx_integrations_workspace",
        "integrations",
        ["workspace_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # Executions table
    op.create_table(
        "executions",
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
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("platform_run_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("workflow_name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("node_count", sa.Integer(), nullable=True),
        sa.Column("items_processed", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_node", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("outcome_valid", sa.Boolean(), nullable=True),
        sa.Column("outcome_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "idx_executions_workspace_created",
        "executions",
        ["workspace_id", "created_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_executions_integration_created",
        "executions",
        ["integration_id", "created_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_executions_status",
        "executions",
        ["workspace_id", "status"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_executions_payload_gin", "executions", ["raw_payload"], postgresql_using="gin"
    )
    op.create_unique_constraint(
        "uq_executions_integration_run_id", "executions", ["integration_id", "platform_run_id"]
    )

    # Monitors table
    op.create_table(
        "monitors",
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
            sa.ForeignKey("integrations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("monitor_type", sa.Text(), nullable=False),
        sa.Column("ping_url", sa.Text(), nullable=True, unique=True),
        sa.Column("expected_cron", sa.Text(), nullable=True),
        sa.Column("grace_period_s", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("expected_outcome", postgresql.JSONB(), nullable=True),
        sa.Column("last_ping_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("alert_on_miss", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Credentials table
    op.create_table(
        "credentials",
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
            sa.ForeignKey("integrations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("token_enc", postgresql.BYTEA(), nullable=True),
        sa.Column("refresh_enc", postgresql.BYTEA(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("gcp_status", sa.Text(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "idx_credentials_expiry",
        "credentials",
        ["workspace_id", "expires_at"],
        postgresql_where=sa.text("deleted_at IS NULL AND status != 'expired'"),
    )

    # Alerts table
    op.create_table(
        "alerts",
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
            sa.ForeignKey("integrations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "monitor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("monitors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "credential_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=True),
        sa.Column("suggested_fix", sa.Text(), nullable=True),
        sa.Column("auto_remediated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("remediation_log", postgresql.JSONB(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "idx_alerts_workspace_created",
        "alerts",
        ["workspace_id", "created_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_alerts_unresolved",
        "alerts",
        ["workspace_id", "severity"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )

    # HITL Requests table
    op.create_table(
        "hitl_requests",
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
            "alert_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alerts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("state_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("proposed_fix", postgresql.JSONB(), nullable=True),
        sa.Column("slack_message_ts", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("reviewer", sa.Text(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("hitl_requests")
    op.drop_table("alerts")
    op.drop_index("idx_alerts_workspace_created", table_name="alerts")
    op.drop_index("idx_alerts_unresolved", table_name="alerts")
    op.drop_index("idx_credentials_expiry", table_name="credentials")
    op.drop_table("credentials")
    op.drop_table("monitors")
    op.drop_table("executions")
    op.drop_index("idx_executions_workspace_created", table_name="executions")
    op.drop_index("idx_executions_integration_created", table_name="executions")
    op.drop_index("idx_executions_status", table_name="executions")
    op.drop_index("idx_executions_payload_gin", table_name="executions")
    op.drop_index("idx_integrations_workspace", table_name="integrations")
    op.drop_table("integrations")
    op.drop_table("workspaces")
