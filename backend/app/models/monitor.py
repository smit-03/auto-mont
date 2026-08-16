"""
Monitor model - Heartbeat / dead-man's switch monitors.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.integration import Integration
    from app.models.workspace import Workspace


class Monitor(Base):
    """Heartbeat / dead-man's switch monitors."""

    __tablename__ = "monitors"

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
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integrations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Narrows an outcome/schema monitor to a single workflow within its
    # integration. NULL means "not workflow-scoped", which is the only sensible
    # value for heartbeat and cron monitors — those are triggered by an inbound
    # ping, not by a workflow's output.
    #
    # A structural column rather than a key inside expected_outcome: JSONB has no
    # schema validation, so a misspelled key would be accepted, stored, and then
    # silently match nothing. A monitor that looks configured and quietly watches
    # nothing is the exact failure class this product exists to catch.
    # Not a foreign key — workflow_id is a platform-native string owned by n8n or
    # Make, not a row in this database.
    workflow_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    monitor_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # heartbeat | outcome | cron | schema
    # The token is its own indexed column, matched by equality. It used to be
    # extracted from ping_url with a LIKE '%token%' substring scan, which was
    # unindexed (a full table scan on every public request), could match the
    # wrong monitor when one token was a substring of another URL, and raised
    # MultipleResultsFound — a 500 on an unauthenticated endpoint — when it did.
    ping_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    ping_url: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    expected_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    grace_period_s: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=300,
        server_default="300",
    )
    expected_outcome: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_ping_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )  # healthy | degraded | failing
    alert_on_miss: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    workspace: Mapped["Workspace"] = relationship(
        "Workspace",
        back_populates="monitors",
        lazy="raise",
    )
    integration: Mapped[Optional["Integration"]] = relationship(
        "Integration",
        back_populates="monitors",
        lazy="raise",
    )

    # Declared here as well as in the migration: create_all() runs outside
    # production, so a model-only omission gives dev and prod different schemas.
    __table_args__ = (
        Index(
            "idx_monitors_workflow_scope",
            "integration_id",
            "workflow_id",
            postgresql_where=workflow_id.is_not(None) & deleted_at.is_(None),
        ),
    )

    def __repr__(self) -> str:
        return f"<Monitor(id={self.id}, name={self.name}, type={self.monitor_type}, status={self.last_status})>"
