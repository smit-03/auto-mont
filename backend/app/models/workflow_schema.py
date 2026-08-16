"""
Workflow schema baseline — the current field shape of a workflow's output.

Schema drift is failure taxonomy class 4 (Data Quality & Schema). The failure it
catches is the one that never raises: an upstream API renames ``customer_email``
to ``email``, every execution keeps reporting success, and the downstream
database quietly receives nulls in that column for days.

One row per (integration, workflow) holding the *current* fingerprint, not a
history. The Blueprint's rule is "alert when the fingerprint changes between
consecutive successful runs", which only ever compares against the immediately
preceding shape — so keeping a row per execution would store a large amount of
data nothing reads.

The baseline advances only on successful executions. A failed run's output is
truncated at the point of failure, so fingerprinting it would record the
truncation as a field removal and then report the recovery as a field addition:
two false alerts for one real failure that the rule engine already caught.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkflowSchema(Base):
    """Current observed output schema for one workflow."""

    __tablename__ = "workflow_schemas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # SHA-256 over the sorted "path:type" list. Compared before the field maps
    # are diffed, so an unchanged workflow costs one string comparison.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # {dotted.path: json_type} for the fields observed in the last successful run.
    fields: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    # The run this baseline was taken from, so a drift alert can point at the
    # execution whose payload defined the "before" side of the comparison.
    sample_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # No ORM relationships: nothing traverses to this table, and the Phase 1
    # lazy="raise" convention exists precisely to keep it that way.
    __table_args__ = (
        # The drift check is a read-then-write and races between two concurrent
        # polls of the same integration exactly as the execution upsert does.
        # Declared here and not only in the migration — create_all() runs
        # outside production, so a model-only omission leaves dev unprotected.
        UniqueConstraint(
            "integration_id",
            "workflow_id",
            name="uq_workflow_schemas_integration_workflow",
        ),
        Index("idx_workflow_schemas_workspace", "workspace_id", "workflow_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<WorkflowSchema(workflow_id={self.workflow_id}, "
            f"fingerprint={self.fingerprint[:12]}, fields={len(self.fields or {})})>"
        )
