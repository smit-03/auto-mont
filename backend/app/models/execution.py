"""
Execution model - Normalized workflow execution records from all platforms.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.integration import Integration
    from app.models.workspace import Workspace


class Execution(Base):
    """Normalized execution records from all platforms."""

    __tablename__ = "executions"

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
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    platform_run_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # success | error | running | timeout | silent_fail
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_node: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    outcome_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcome_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        back_populates="executions",
        lazy="raise",
    )
    integration: Mapped["Integration"] = relationship(
        "Integration",
        back_populates="executions",
        lazy="raise",
    )

    # Indexes
    __table_args__ = (
        # The polling upsert does SELECT-then-INSERT, which races: two concurrent
        # polls of the same integration can both see "absent" and both insert.
        # This constraint is the actual guarantee. It must be declared here and
        # not only in the Alembic migration — init_db() calls create_all() in
        # non-production, so a model-only omission means dev and prod run
        # different schemas, and dev is the one missing the protection.
        UniqueConstraint(
            "integration_id",
            "platform_run_id",
            name="uq_executions_integration_run_id",
        ),
        Index(
            "idx_executions_workspace_created",
            "workspace_id",
            "created_at",
            postgresql_using="btree",
        ),
        Index(
            "idx_executions_integration_created",
            "integration_id",
            "created_at",
            postgresql_using="btree",
        ),
        Index(
            "idx_executions_status", "workspace_id", "status", postgresql_where=deleted_at.is_(None)
        ),
        Index("idx_executions_payload_gin", "raw_payload", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<Execution(id={self.id}, platform={self.platform}, run_id={self.platform_run_id}, status={self.status})>"
