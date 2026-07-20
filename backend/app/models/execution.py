"""
Execution model - Normalized workflow execution records from all platforms.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
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
        lazy="selectin",
    )
    integration: Mapped["Integration"] = relationship(
        "Integration",
        back_populates="executions",
        lazy="selectin",
    )

    # Indexes
    __table_args__ = (
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
        # Unique constraint
        # Note: unique constraint on (integration_id, platform_run_id) is defined via __table_args__
    )

    def __repr__(self) -> str:
        return f"<Execution(id={self.id}, platform={self.platform}, run_id={self.platform_run_id}, status={self.status})>"
